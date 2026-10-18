# SMC swing model — OOS tables (generated 2026-10-18T09:11:02+00:00, data to 2026-10-18)

Variants tried: 42; OOS hypotheses: 6; Bonferroni z = 2.638.

| pair | model | variant | IS n · E taker | OOS n | win rate [95 %] | E[R] taker [95 %] | E[R] maker | E[R] gross | max DD (R) | trades / month / symbol | random control E (p05–p95) | pass |
|---|---|---|---|---:|---|---|---|---:|---:|---:|---|---|
| 4h>15m | HTF POI + LTF CHoCH | {"ltf_len": 3, "poi": "fvg", "sweep": true} | 109 · -0.0856 | 44 | 31.8 % [20.0–46.6] | -0.503 [-0.86, -0.14] | -0.366 [-0.73, -0.01] | -0.2749 | 22.83 | 0.62 | -0.271 (-0.69…+0.18) | no |
| 4h>15m | LTF CHoCH only | {"ltf_len": 3} | 5293 · -0.2082 | 2238 | 31.9 % [30.0–33.9] | -0.294 [-0.37, -0.22] | -0.127 [-0.20, -0.05] | -0.0165 | 666.11 | 31.61 | — | no |
| 1h>5m | HTF POI + LTF CHoCH | {"ltf_len": 2, "poi": "fvg", "sweep": false} | 665 · -0.4154 | 270 | 31.1 % [25.9–36.9] | -0.501 [-0.72, -0.28] | -0.203 [-0.41, -0.00] | -0.0045 | 137.24 | 3.81 | -0.687 (-0.96…-0.44) | no |
| 1h>5m | LTF CHoCH only | {"ltf_len": 3} | 15863 · -0.4396 | 6653 | 29.4 % [28.4–30.6] | -0.612 [-0.66, -0.57] | -0.291 [-0.33, -0.25] | -0.0763 | 4080.52 | 93.97 | — | no |
| 1d>1h | HTF POI + LTF CHoCH | {"ltf_len": 2, "poi": "any", "sweep": true} | 33 · 0.0439 | 28 | 39.3 % [23.6–57.6] | -0.046 [-0.65, +0.55] | +0.036 [-0.56, +0.63] | 0.0906 | 10.05 | 0.4 | -0.024 (-0.60…+0.45) | no |
| 1d>1h | LTF CHoCH only | {"ltf_len": 2} | 1608 · -0.114 | 666 | 34.1 % [30.6–37.8] | -0.103 [-0.27, +0.06] | -0.015 [-0.18, +0.15] | 0.0434 | 126.31 | 9.41 | — | no |

Per symbol (OOS, taker):

- 4h>15m filtered: BTCUSDT n 16 E -0.9227 · ETHUSDT n 14 E -0.0883 · SOLUSDT n 14 E -0.4366
- 4h>15m unfiltered: BTCUSDT n 776 E -0.2996 · ETHUSDT n 780 E -0.3842 · SOLUSDT n 682 E -0.1837
- 1h>5m filtered: BTCUSDT n 90 E -0.7586 · ETHUSDT n 98 E -0.607 · SOLUSDT n 82 E -0.093
- 1h>5m unfiltered: BTCUSDT n 2259 E -0.8742 · ETHUSDT n 2315 E -0.5275 · SOLUSDT n 2079 E -0.4215
- 1d>1h filtered: BTCUSDT n 9 E -0.2001 · ETHUSDT n 5 E 0.3441 · SOLUSDT n 14 E -0.0864
- 1d>1h unfiltered: BTCUSDT n 228 E -0.2697 · ETHUSDT n 237 E -0.1522 · SOLUSDT n 201 E 0.1438
