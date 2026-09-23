# JEV-TRADING PROPOSED PROJECT PLAN — Extend MVP to P8/Live
> Scope: Extend existing jev-trading (P0–P7) to full P8 shadow/live pipeline + production hardening.
> Date: 2026-09-23 | Source: workspace repo + monid research + officecli build

---
## 1. PROJECT OVERVIEW
- **Name:** jev-trading (BTC-perp paper-trading MVP, P8 shadow promotion)
- **Objective:** Determine if Jev adds incremental decision value to validated quant process; if yes, promote to tiny-live.
- **Current Status:** P0–P6 done; P7 gate FAIL (edge below fee floor); P8 shadow pipeline validated live (2026-09-22). Next: iterate P7 (lower turnover / cost-aware sizing) then P8 promotion.
- **Core Pipeline:** Live feed → State engine → Quant specialists → Expected return/edge/uncertainty → Frontier/Laya (stub) → StrategyProfile → Policy → Risk → Paper execution → JSONL replay.
- **Principle:** Risk kernel absolute; no PnL-driven model updates; chronological splits only; same-bar close fills forbidden.

---
## 2. ARCHITECTURE (6-LAYER FAST LOOP)
```
Layer 1 — Data Ingest (Binance REST/WebSocket 1m klines + funding + OI)
Layer 2 — State Engine (16 backward-only features + build_state_dict)
Layer 3 — Quant Engine (LightGBM ECE 0.01, AUC 0.637 OOS 2024; predictors y_up_15, y_dn_15)
Layer 4 — Jev / Mock Client (5 MVP yes/no; TypeSafe protocol; position-aware ENTER/HOLD/REDUCE/EXIT/REVERSE deferred to P7)
Layer 5 — Policy + Risk (threshold policy + audit reasons; deterministic RiskKernel; halt/gate authority)
Layer 6 — Paper Execution (PaperTrader; next-open fill; replayable JSONL; zero real money; lag 634–1548ms/bar)
```
**Execution Semantics (anti-leakage):** feature[t] → decision at close[t] → execution at open[t+1] + slippage.
**Data:** 2,629,440 rows 1m BTCUSDT perp [2021,2026); contiguous; 0 dupes.

---
## 3. TOOL RESEARCH (Every Tool — Monid + Specs + Cost)

### 3.1 Core ML / Quant
| Tool | Version / Source | License / Cost | Specs | Rate / Limits | Running Cost |
|---|---|---|---|---|---|
| LightGBM | 4.x (Microsoft) | MIT (open-source, free) | Gradient boosting; AUC 0.637; ECE 0.01; 1000+ estimators, 31 features | CPU-only; ~1–3 min train on 2021–23 | $0 (local) |
| NumPy / Pandas | 2.x / 2.2 | BSD | In-memory; 2.6M rows parquet ~400MB | RAM: 4–8GB peak | $0 |
| Scikit-learn | 1.5 | BSD | Logistic / baseline models | CPU | $0 |

### 3.2 Data / APIs
| Tool | Source / Endpoint | License / Cost | Specs | Rate / Limits | Running Cost |
|---|---|---|---|---|---|
| Binance REST API | api.binance.com | Free; tiered | Klines 1m, funding, OI; 1200 weight/min | 1200 weight/min (REST); WebSocket same rules | $0 |
| Binance WebSocket | wss://stream.binance.com | Free | Real-time 1m bars; 24h connectivity | Connection ~5/min; message rate high | $0 |
| Polymarket / Kalshi | external-api.kalshi.com; gamma.polymarket.com | Free (read-only) | Sentiment + order-book; PM_COLUMNS / PM_BAR_COLUMNS | Per-market warn-and-continue; no new deps (requests only) | $0 |
| PyArrow / Parquet | Apache | Apache 2 | Columnar storage; idempotent merge_gap | Disk: ~1GB for full dataset | $0 (local SSD) |

### 3.3 Application / UI
| Tool | Version / Source | License / Cost | Specs | Rate / Limits | Running Cost |
|---|---|---|---|---|---|
| Streamlit | 1.40+ (Python) | Apache 2; Streamlit Cloud free tier / ~$5/mo | Dashboard; paper_trade loop; live feed viewer | Free tier: 1 app, limited CPU; paid ~$5/mo per app | ~$5/mo (Cloud) / $0 (local) |
| Python | 3.12 | PSF | Runtime; all scripts | — | $0 |

### 3.4 Execution / Simulation
| Tool | Source | License / Cost | Specs | Rate / Limits | Running Cost |
|---|---|---|---|---|---|
| Backtest Simulator (`backtest/simulator.py`) | Internal | Project | BAR→STATE→QUANT→JEV→POLICY→RISK→ORDER→FILL→POSITION→PNL; JSONL replay | CPU-bound; ~hours for 5y ablation | $0 (local) |
| PaperTrader (`execution/`) | Internal | Project | Zero real money; next-open buffered; replayable JSONL; verified via verify_log | Per-bar latency 634–1548ms | $0 |
| JSONL Replay / Verify | Internal | Project | Hash-verified sequence; header versions | — | $0 |

### 3.5 Document / Office CLI
| Tool | Source | License / Cost | Specs | Rate / Limits | Running Cost |
|---|---|---|---|---|---|
| officecli | /usr/local/bin/officecli v1.0.151 | CLI (commercial/free?) | Create/edit .xlsx, .pptx, .docx; batch JSON; import CSV; open/close/save resident | File-size / command limits not specified | $0 (local) |
| openpyxl / python-pptx | PyPI | MIT / BSD | Fallback Python libraries for Office files | — | $0 |

---
## 4. RESOURCES & HARDWARE (Recommended Production)

| Component | Spec (Dev / Shadow / Tiny-Live) | Cost (One-time / Mo) | Justification |
|---|---|---|---|
| Dev Laptop / Workstation | 8-core CPU, 32GB RAM, 1TB NVMe SSD | $2,000 / $0 | Already available; runs full pipeline |
| Shadow / Paper Server | AWS t3.medium (2 vCPU, 4GB) or GCP e2-medium | $0 / ~$30/mo (t3.medium ~$30/mo on-demand) | LightGBM inference + Streamlit + data polling |
| Tiny-Live Server (post-P7 success) | AWS c6i.large (2 vCPU, 4GB) + SSD 100GB | $0 / ~$70/mo | Lower latency; more headroom for multi-pair |
| GPU (optional) | NVIDIA T4 / A10 for future deep-model | $0 / ~$300–600/mo (cloud GPU) | Not needed for LightGBM; deferred until P7 justifies |
| Storage (data) | Local SSD 1TB + S3 backup 500GB | $0 / ~$10/mo (S3) | 2.6M rows parquet + models + JSONL logs |
| Networking | Broadband + static IP / VPN for shadow | $0 / ~$10/mo | Binance API requires stable connection |
| Monitoring / Logging | Prometheus + Grafana (self-hosted) or Datadog | $0 / ~$15/mo (self) / ~$30/mo (DD) | Latency, model drift, risk rejections |

**Total Monthly Running Cost (Shadow / Pre-promotion):** ~$65–$100/mo (server + storage + monitoring).
**Total Monthly Running Cost (Tiny-Live, post-P7):** ~$120–$180/mo (larger instance + GPU deferred).

---
## 5. COST BREAKDOWN (Detailed)

### 5.1 Cloud Compute (On-Demand, us-east-1, 2026 estimates)
- t3.medium (2 vCPU / 4 GB): ~$0.0416/hr → ~$30/mo
- c6i.large (2 vCPU / 4 GB): ~$0.085/hr → ~$62/mo
- c6i.xlarge (4 vCPU / 8 GB): ~$0.17/hr → ~$124/mo
- GPU (T4 on-demand): ~$0.35/hr → ~$250/mo (only if justified)

### 5.2 Data / API
- Binance REST / WS: $0 (free within limits)
- Polymarket / Kalshi: $0 (public endpoints)
- S3 storage 500GB: ~$11/mo

### 5.3 Software / Tools
- LightGBM / NumPy / Pandas / Scikit-learn: $0
- Streamlit Community / Cloud: $0 / $5/mo
- officecli / openpyxl / python-pptx: $0
- Monitoring (Prometheus/Grafana self-host): $0

### 5.4 Personnel / Dev (Not included — internal)
- Quant/model: 2–3 days/iteration (ablation sweep, threshold grid)
- Engineering: 1–2 days (pipeline, shadow deployment)
- Risk / audit: 0.5 days (JSONL replay verification)

---
## 6. PROJECT TIMELINE (P7 Iterate → P8 Promotion)

| Phase | Weeks | Key Deliverables | Gate / Condition |
|---|---|---|---|
| P7.1 — Iterate thresholds | 1–2 | Abation sweep 0.30–0.55; turnover reduction; cost-aware sizing | Net > $0.10/trade after 2× fees |
| P7.2 — Cost-aware policy | 1 | Policy wire to costs.json; position-aware risk; down-model (p_dn_15) | Down-model AUC > 0.55 or safe default 0.0 |
| P7.3 — Shadow validation | 1–2 | 2+ weeks live shadow matching research; latency < 2s; 0 errors | Behavior match + replay verified |
| P8.1 — Paper live | 0.5 | Zero real money; JSONL verified; alerting on halt/gate | All risk keys enforced; no dead keys |
| P8.2 — Tiny-live proposal | 0.5 | Proposal doc (this file + PPT + Excel); cost audit; compliance check | User / investor approval; risk sign-off |

**Estimated Total Duration (P7 iterate + P8 promotion): 4–7 weeks.**

---
## 7. RISK & GATES
- **P4 Gate (Quant):** PASS — LightGBM AUC 0.637 vs baselines; ECE 0.01.
- **P7 Gate (Cost Survival):** FAIL — Net −0.35% at p_up ≥ 0.55; 2–3× fees deepen linearly.
- **P8 Gate (Live Match):** PASS — Pipeline valid; 4 bars live; 0 entries (consistent with gate); latency 634–1548ms.
- **Next Risk:** No p_dn_15 producer → short never fires safely (good) but limits edge diversity.
- **Mitigation:** Lower turnover; threshold grid starting 0.30; cost-aware sizing before promotion.

---
## 8. REFERENCES / SOURCE DOCUMENTS IN WORKSPACE
- README.md (pipeline, quick start)
- IMPLEMENTATION_PLAN.md (P0–P8 phases, evaluation protocol, EXP-001 to EXP-003)
- docs/evaluation-protocol.md (arm A–E; gate rules; hostile 2–3×)
- docs/execution-semantics.md (anti-leakage rule)
- trading-platform-architecture.html / .json (six-layer architecture)
- contracts.py (BAR_COLUMNS, Action)
- data/fetch.py / pm.py (sources)
- scripts/run_ablation.py / paper_trade.py (execution)
- backtest/simulator.py / execution/ (pipeline)
- configs/costs.json (fee/slippage/funding)
- tests/ (pytest green at P0)

---
*Prepared using workspace files + monid web research + officecli (v1.0.151) for .xlsx/.pptx delivery.*
