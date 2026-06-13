# S2 アレルゲン交差汚染（T9）— 宣言的仕様

> コードは `src/orx/exp/suites/s2_allergen/`（ADR-013）。authoritative=SCENARIOS.md。

- 試金石: ②関係伝播（接触の推移閉包）＋時間付き状態推論 / 仮説: H5 / Tier: A
- 世界: `configs/world/s2_allergen.yaml`（源製品・グリッパ・トレイ・観測ロボット）
- 実験: `configs/experiments/s2_allergen.yaml`
- 条件: OR-full / OR-belief（洗浄リセット無視）/ B1（横断融合なし）/ B0（履歴なし）
- 真値導出: `orx.oracle.scenarios.s2.contamination_closure`（ADR-015。アレルゲン型・対称・洗浄リセット・前方向）
- X2 接触蒸留: `orx.perception.contacts.distill_contacts`（`contact_miss_rate` ノブ）
- 語彙: orx-st:ContactEvent/contactParty/possiblyContaminatedBy, orx-cap:CleaningEvent/cleans, orx-biz:AllergenClass（+SHACL+CQ）
- demo: `uv run orx scenario demo s2` / 実験: `uv run orx scenario run configs/experiments/s2_allergen.yaml`

## 失敗予言（反証テスト）
- B0 は履歴なしで推移閉包を解けず汚染把持を許可（安全違反＝偽陰性）。
- B1 は横断融合が無く越境連鎖の汚染を取りこぼす（安全違反）。
- OR−belief は洗浄リセットを無視し洗浄後の許可把持を拒否（過保守＝偽陽性）。
- OR-full は推移閉包＋洗浄で違反0・過保守0・汚染F1=1.0。

## 受入基準（SCENARIOS.md §4 逐語）
ノイズ0で汚染集合F1=1.0・違反0。見落とし率掃引で OR-full と OR−belief の頑健性曲線が分離。
