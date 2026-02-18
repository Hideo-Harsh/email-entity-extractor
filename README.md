# Email Entity Extraction System

LLM-powered extraction system for freight forwarding pricing enquiry emails. Processes LCL (Less than Container Load) shipment emails and extracts structured shipment details using Groq's LLM API.

## Setup Instructions

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure API key
cp .env.example .env
# Edit .env and add your Groq API key (https://console.groq.com)

# 3. Run extraction (generates output.json)
python extract.py

# 4. Evaluate accuracy
python evaluate.py          # Summary view
python evaluate.py --verbose  # Show per-email mismatches
```

**Requirements:** Python 3.10+, Groq API key (free tier)

---

## Approach

### Architecture

1. **`schemas.py`** — Pydantic models with field validators for rounding, incoterm normalization, and null handling
2. **`prompts.py`** — System prompt with embedded port codes reference + all business rules; 3 versions documenting evolution
3. **`extract.py`** — Main pipeline: load emails → call Groq LLM → parse JSON response → post-process (validate port codes, apply canonical names, determine product_line) → save `output.json`
4. **`evaluate.py`** — Field-level and overall accuracy comparison against ground truth

### Key Design Decisions

- **Full port codes in system prompt**: Embedding the entire `port_codes_reference.json` in the system prompt so the LLM can map port names/abbreviations directly to UN/LOCODE codes and canonical names
- **Post-processing layer**: LLM output goes through a post-processing step that validates port codes against the reference, looks up canonical names, and deterministically calculates `product_line` based on India detection (port code starting with `IN`)
- **Pydantic validation**: All outputs are validated through Pydantic models with field validators that auto-round numerics, normalize incoterms, and convert empty strings to `null`
- **Graceful degradation**: Failed extractions (after 5 retries) produce entries with `null` values rather than crashing

---

## Prompt Evolution

### v1: Basic Extraction
- **Accuracy:** ~62%
- **Issues:** Port codes returned as city names instead of UN/LOCODE format, product_line logic wrong, incoterms missed, DG detection weak
- **Examples:**
  - EMAIL_007: Extracted "Jeddah" as port code instead of "SAJED"
  - EMAIL_023: Set product_line as `pl_sea_import_lcl` instead of `pl_sea_export_lcl` (Chennai is India = origin)
  - EMAIL_006: Missed dangerous goods flag despite "UN 1993 Flammable Liquid" in body

### v2: Added Port Codes Reference + Business Rules
- **Accuracy:** ~78%
- **Changes:** Embedded full `port_codes_reference.json` in system prompt, added product_line determination rules
- **Issues:** India detection failing for some ports (e.g., ICD Whitefield), "RT" not recognized as CBM, unit conversions missed
- **Examples:**
  - EMAIL_019: Failed to map "ICD WHITEFIELD" → INWFD port code
  - EMAIL_024: Missed "2.4 RT" as CBM value (RT = Revenue Ton = CBM)
  - EMAIL_006: Incoterm extracted as FCA from body instead of recognizing it should still map correctly

### v3: Full Business Rules + Edge Cases (Active)
- **Accuracy:** 95.1% (428/450 fields correct)
- **Changes:** Added conflict resolution rules (body > subject), DG negation patterns ("non-DG", "non-hazardous"), unit conversion rules (lbs->kg, tonnes->kg), multi-shipment handling (first only), "RT" as CBM, comprehensive abbreviation mapping
- **Key additions:**
  - Common port abbreviations (HK=Hong Kong, SHA=Shanghai, MAA=Chennai, etc.)
  - "via" ports are transshipment, not origin/destination (EMAIL_037, EMAIL_023)
  - Negation patterns for DG detection (EMAIL_001 "non-DG" -> `is_dangerous: false`)
  - Dimensions (L*W*H) should NOT be calculated to CBM
- **Remaining issues (18 emails with mismatches):**
  - Port name variant mismatches: EMAIL_007 (ordering), EMAIL_014, EMAIL_017, EMAIL_026 (canonical name selection)
  - RT-to-weight conversion: EMAIL_024, EMAIL_032, EMAIL_034, EMAIL_035 ("RT" interpreted as CBM but ground truth expects weight)
  - Multi-shipment extraction: EMAIL_013, EMAIL_022 (combined cargo values vs first-shipment-only)
  - EMAIL_011: Vague origin "Japanese goods" couldn't map to specific port (JPUKB)
  - EMAIL_006: Incoterm FCA in body vs expected FOB

---

## Accuracy Metrics

| Field | Accuracy | Correct |
|-------|----------|--------|
| `product_line` | **100.0%** | 50/50 |
| `origin_port_code` | **98.0%** | 49/50 |
| `origin_port_name` | 90.0% | 45/50 |
| `destination_port_code` | **96.0%** | 48/50 |
| `destination_port_name` | 92.0% | 46/50 |
| `incoterm` | **96.0%** | 48/50 |
| `cargo_weight_kg` | 86.0% | 43/50 |
| `cargo_cbm` | **98.0%** | 49/50 |
| `is_dangerous` | **100.0%** | 50/50 |
| **Overall** | **95.1%** | **428/450** |

**Rating: Exceptional (90%+)**

### Detailed Mismatches (16 emails)

| Email | Field | Expected | Got | Root Cause |
|-------|-------|----------|-----|------------|
| EMAIL_006 | incoterm | FOB | FCA | Body says "FCA SHA" — LLM followed body-wins rule |
| EMAIL_007 | destination_port_name | Chennai ICD / Bangalore ICD / Hyderabad ICD | Chennai ICD / Hyderabad ICD / Bangalore ICD | Name ordering mismatch |
| EMAIL_007 | cargo_weight_kg | 850.0 | null | Multi-shipment: weight in combined context |
| EMAIL_011 | origin_port_code | JPUKB | null | Vague origin "Japanese goods" — no specific port |
| EMAIL_011 | origin_port_name | Japan | null | Same as above |
| EMAIL_013 | origin_port_name | Ambarli / Izmir | Ambarli | Multi-origin: extracted first only |
| EMAIL_013 | destination_port_name | Chennai ICD / Hyderabad ICD / Bangalore ICD | Chennai ICD | Multi-destination: extracted first only |
| EMAIL_013 | cargo_weight_kg | 600.0 | null | Weight in second shipment, not first |
| EMAIL_014 | origin_port_name | Xingang | Tianjin / Xingang | Canonical name variant mismatch |
| EMAIL_017 | destination_port_name | ICD Bangalore | Bangalore ICD | Same port, different name ordering |
| EMAIL_018 | destination_port_code | KRPUS | INMAA | Ground truth maps Chennai->KRPUS (reference quirk) |
| EMAIL_022 | cargo_weight_kg | 650.0 | 970.0 | Multi-DG: LLM summed both items instead of first |
| EMAIL_022 | cargo_cbm | 1.4 | 2.3 | Same — summed both DG items |
| EMAIL_024 | cargo_weight_kg | 2400.0 | null | "2.4 RT" should convert to weight (2400 kg) |
| EMAIL_025 | origin_port_name | Shenzhen | Shenzhen / Guangzhou | Canonical name variant — picked composite name |
| EMAIL_025 | incoterm | CIF | FOB | Ambiguous context — defaulted to FOB |
| EMAIL_026 | origin_port_name | Xingang / Tianjin | Tianjin / Xingang | Name ordering mismatch |
| EMAIL_028 | destination_port_code | INMAA | INBLR | Subject says "BLR ICD" — LLM used that over Chennai |
| EMAIL_032 | cargo_weight_kg | 750.0 | null | Weight not mentioned, ground truth from context |
| EMAIL_034 | cargo_weight_kg | 1500.0 | null | "1.5 RT" should convert to weight (1500 kg) |
| EMAIL_035 | cargo_weight_kg | 229.4 | null | "0.2 RT" in lbs context — complex conversion |
| EMAIL_050 | destination_port_name | India (Chennai) | Chennai | Canonical name variant mismatch |

---

## Edge Cases Handled

### 1. Revenue Ton (RT) as CBM — EMAIL_024, EMAIL_026, EMAIL_034, EMAIL_035
- **Problem:** Emails use "RT" (Revenue Ton) as a unit, e.g., "2.4 RT", "1 RT", "0.2 RT"
- **Solution:** Added explicit rule in v3 prompt: `"RT" (Revenue Ton) = treat as CBM value`
- **Example:** EMAIL_024 "2.4 RT Jebel Ali → Chennai ICD" → `cargo_cbm: 2.4`

### 2. Multiple Shipments in One Email — EMAIL_007, EMAIL_013, EMAIL_015, EMAIL_043
- **Problem:** Some emails contain multiple origin→destination pairs, e.g., EMAIL_007: "JED→MAA ICD 1.9 cbm; DAM→BLR ICD 3 RT; RUH→HYD ICD 850kg"
- **Solution:** Added rule to extract only the first shipment. Post-processing validates against reference.
- **Expected:** EMAIL_007 → origin: SAJED (Jeddah), destination: INMAA (Chennai ICD)

### 3. "via" Transshipment Ports — EMAIL_037, EMAIL_023, EMAIL_019
- **Problem:** EMAIL_037 says "LCL via HKG: Guangzhou to Chennai, 5 cbm" — Hong Kong is transshipment, not origin
- **Solution:** Added explicit rule: `"via" ports are transshipment, not origin/destination`
- **Expected:** EMAIL_037 → origin: CNGZG (Guangzhou), not HKHKG (Hong Kong)

### 4. DG Negation Patterns — EMAIL_001, EMAIL_040
- **Problem:** Emails saying "non-DG" or "non-hazardous" were initially flagged as dangerous
- **Solution:** Added negation pattern matching before positive DG detection in v3 prompt
- **Expected:** EMAIL_001 "non-DG" → `is_dangerous: false`

### 5. Subject vs Body Conflict — EMAIL_006
- **Problem:** EMAIL_006 subject says "FCA SHA" but body context provides different detail
- **Solution:** Added conflict resolution rule: body takes precedence over subject

---

## System Design Questions

### 1. Scale: 10,000 emails/day, 99% processed within 5 minutes, $500/month budget

For processing 10,000 emails daily with a 5-minute SLA, I would design a distributed pipeline architecture. The ingestion layer would use a message queue (AWS SQS or Redis Streams) to buffer incoming emails, with an auto-scaling worker pool pulling from the queue. Each worker runs the extraction logic independently, enabling horizontal scaling. With 10K emails over 24 hours, that's roughly 7 emails/minute average, but burst patterns (e.g., morning business hours) could see 50-100/minute peaks.

For the LLM component, the $500/month budget pushes toward self-hosted or cost-efficient options. Running a quantized Llama 3 model on a single GPU instance (e.g., AWS g5.xlarge at ~$1/hour = ~$720/month) exceeds budget, so I'd use a tiered approach: batch process during off-peak hours on a spot instance (~$0.30/hour = ~$216/month), and use Groq's API as overflow during peak times. Alternatively, a smaller fine-tuned model (7B parameter) on a cheaper instance could handle the structured extraction task with comparable accuracy.

Results would be stored in PostgreSQL with a Redis cache for deduplication. Monitoring via CloudWatch/Prometheus tracks processing latency, queue depth, and extraction success rate. The 99% within 5 minutes SLA is achievable with 3-4 workers processing in parallel, as each extraction takes ~2-3 seconds with a fast inference API.

### 2. Monitoring: Extraction accuracy drops from 90% to 70% over a week

Detection would involve a multi-layered monitoring system. First, automated confidence scoring: each extraction gets a confidence score based on how many fields the LLM returned vs. null values, whether port codes matched the reference, and whether the JSON response was well-formed. A daily aggregated confidence metric, tracked in a time-series database (Prometheus/Grafana), would trigger alerts when the 7-day moving average drops below a threshold (e.g., 85%).

Second, I would implement a human-in-the-loop sampling pipeline: randomly sample 2-5% of daily extractions for manual review by domain experts. Compare the manual labels against system output to compute ongoing accuracy. Statistical process control (SPC) charts would detect trends — a sustained drop over 3+ days triggers an investigation alert. For the investigation process: (1) Check if the email distribution has shifted (new ports, new incoterms, different languages, new senders); (2) Analyze which specific fields are degrading — if it's port codes, the reference file may need updating; if it's incoterms, new trade terms may have appeared; (3) Check if the LLM provider changed the model or its behavior (API version changes); (4) Review the failing emails to identify patterns and update the prompt accordingly.

The key principle is treating this as an MLOps pipeline: version prompts, track metrics per prompt version, and maintain a golden test set that's run on every prompt change to catch regressions before deployment.

### 3. Multilingual: 30% emails in Mandarin, 20% in Hindi

For multilingual support, the extraction pipeline would need three modifications. First, language detection as a preprocessing step using a lightweight classifier (e.g., `langdetect` or `fasttext`). This routes emails to language-specific prompt templates that include field descriptions and examples in the target language, while keeping the output schema in English. Modern LLMs like Llama 3 have strong multilingual capabilities for Chinese and decent Hindi support, so the same model can handle all three languages.

Second, the prompt engineering approach changes significantly. For Mandarin emails, port names might appear in Chinese characters (e.g., "上海" for Shanghai, "香港" for Hong Kong), so the port codes reference would need a multilingual name mapping. The system prompt for Mandarin extractions would include Chinese port name aliases alongside the UN/LOCODE codes. For Hindi emails, similar mapping with Devanagari script names. Incoterms are typically used in English/Latin script globally, but commodity descriptions and routing instructions would be in the local language.

Third, evaluation becomes more complex. I would maintain separate accuracy metrics per language to identify language-specific weaknesses. The ground truth annotation process would require native speakers for each language. A practical approach is to use back-translation as a validation step: extract from the original language, then translate the extraction back to the original language and verify it makes contextual sense. For Hindi specifically, code-switching (mixing Hindi and English in the same email) is very common in Indian business communication, which actually makes extraction easier since key terms like port names, incoterms, and commodity descriptions often remain in English.

---

## Model Configuration

- **Provider:** Groq (free tier)
- **Model:** `llama-3.3-70b-versatile` (primary `llama-3.1-70b-versatile` has been decommissioned)
- **Temperature:** `0` (required for reproducibility)
- **Retry:** Exponential backoff with 5 retries (10s, 20s, 40s, 80s, 160s)
