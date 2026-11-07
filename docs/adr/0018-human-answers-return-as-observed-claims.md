# ADR-0018: 人の確認回答は 4 つ目のコマンドで受け、`observed` の主張イベントにして信念に戻す

- 日付: 2026-11-07
- 状態: 採択
- 触る層: api（コマンド）、knowledge（判断の種別・観測要求の読み出し）、integration（回答イベント）、services（learning-worker）、infra（alembic 0012）

## 文脈

設計書 §14 の手順 4 は「人の回答は `observed` として最上位の事実性を持ち、記録訂正と信頼性モデル更新
（学習信号）に変わる」と定める。スケジューラは人への質問（`HUMAN_QUESTION`）を選べるが、**回答の入口が
無い**。コマンドは `resolve_discrepancy` / `curate_concept` / `execute_action` の 3 つで、観測要求は
積まれて発行されても満たされない（`ObservationQueue.mark_satisfied` を呼ぶ側が無い。H-13、ADR-0011 の未決）。

M8-26 で belief-worker は業務トピックの主張イベント（`BusinessEvent/<fluent>`、`observed`、確率 p）を
記録尤度として信念に入れるようになった（`BeliefIngest` が `RecordEvidence` に監査隔離を適用）。
**人の回答を同じ形のイベントにすれば、信念更新は変更なしで回答を証拠として扱える。**

## 選択肢

### 問い 1: 回答の入口

1. **4 つ目のコマンド `answer_observation` を作業キュー経路（ADR-0012）で受ける。** API は検証して
   `learning_decisions` に積むだけで、適用は learning-worker。冪等の鍵・再試行・失敗の記録が既存の仕組みで揃う。
2. `resolve_discrepancy` の outcome に回答を含める。解決（記録訂正・信頼性更新）と回答（証拠を 1 件足す）は
   判断の重さが違い、観測要求の鍵（不一致に由来しないものもある。例: 期待の期限切れ、ADR-0017）が合わない。
3. API が直接イベントログに送る。ADR-0012 が退けた形（読み取り層に副作用の権限が増える、失敗で判断が失われる）。

### 問い 2: 回答をどう信念に入れるか

1. **取込の主張イベントと同じ形のイベントを `<site>.integration.business` に送る。**
   `type = BusinessEvent/<fluent>`、`modality = observed`、`p` は人の確信度（成立なら c、不成立なら 1 − c）、
   証拠は `RecordEvidence(record=urn:sbp:human-answer:<decision_id>, source=<回答者>)`、`event_id` は
   `decision_id` から決まる（再送で二重にならない。不変条件 4）。信念更新・照合・監査隔離は既存のまま使える。
2. `observed` アサーションを知識グラフに直接書く。信念更新を通らず、`inferred` の現在信念が人の回答と
   食い違ったまま残る（belief-worker が読むのはイベント）。
3. 専用の証拠種別（`HumanEvidence`）を足す。イベントの契約変更（後方互換の検査・スキーマ再生成）に見合わない。
   回答者は `RecordEvidence.source` に入れれば足りる。

### 問い 3: 要求が「満たされた」かどうか（H-13 の人の回答の部分）

1. **回答が要求の鍵 `(discrepancy_id, fluent)` を名指す。鍵の一致で満たす。** 回答がどの要求への回答かを
   人が選ぶので、実体 × フルーエント × 区間の突き合わせは要らない。
2. 実体 × フルーエント × 区間の重なりで推定する。「どれだけ測れば満たされたか」が手段で違う問題（H-13）が残る。

センサ観測が戻ったときの充足は H-13 のまま残す（本 ADR の範囲外）。

## 決定

問い 1: コマンド `answer_observation`。問い 2: 主張イベント。問い 3: 鍵の一致。

- コマンド: `decision_id`（冪等）/ `discrepancy_id`（文字列。期待由来の `expectation:<id>` もある）/ `fluent` /
  `entity` / `state`（成立しているか）/ `note`（必須）/ `answered_by`。
- 適用は 送信 → 充足の記録 → 判断を適用済みに。送信は決定的な ID で冪等なので、失敗して再試行しても二重にならない。
- **キューに無い要求への回答は失敗させる**（誤送信の可能性が高い）。**満たされた要求への回答は受ける**
  （訂正の回答がありうる。充足は冪等）。
- 人の確信度は `Settings.reasoning.human_answer_confidence`（既定 0.98）。1.0 にしない（人も誤る。
  信念は対数オッズで飽和するので、後から別の証拠で覆せる）。
- 記録訂正・信頼性更新は `resolve_discrepancy` のまま。回答は証拠を信念に入れるだけで、解決の判断を自動で
  行わない（回答 1 件で記録の訂正や全照合の尤度を動かさない。ADR-0012 の問い 3 と同じ線引き）。

## 帰結

- **不変条件 3**: 回答の様相は `observed`（人は観測者）。そこから信念を作るのは belief-worker で、出力は `inferred`。
- **不変条件 4**: `event_id` は `decision_id` から決定的な UUIDv7。
- **不変条件 5**: フルーエント型は `vocab` から引き、IRI を直書きしない。
- **不変条件 6**: 証拠が `RecordEvidence`（source = 回答者）なので、既存の `AuditMask` が回答者・回答 ID を除外できる。
- **不変条件 7**: p は人の確信度の設定値で、生スコアではない。
- **不変条件 11**: LLM を使わない。
- **構成**: ワーカー数は 6 のまま。alembic が 11 → 12（`learning_decisions.kind` の CHECK 拡張）。
  learning-worker が Kafka producer を持つ（ingest-worker と同じ `KafkaEventProducer`）。
- **テスト**: 単体（コマンドの検証、イベントの形と決定的 ID、適用の順序と失敗）、統合（API ハンドラ → キュー →
  learning-worker → 業務トピック → belief-worker で信念が回答の向きに動き、要求が満たされる）。

## 未決

- **UI の回答フォーム。** API コマンドまで。画面は別タスク。
- **回答者の権限と確信度。** 誰の回答でも同じ確信度で入る。専門家と現場作業者で変えるかは決めていない。
- **回答からの解決の提案。** 回答が不一致の解決を強く示唆する場合に、解決コマンドの下書きを出すか。
- **センサ観測による充足（H-13 の残り）。**
