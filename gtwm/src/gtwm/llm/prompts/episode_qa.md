# 生成エピソードのQA・LLM-as-judge（タスク6）

あなたは倉庫シミュレーションのQA担当です。以下のエピソードの意図されたシナリオ記述と、
観測された要約（アンカーイベント・ゾーン遷移の要約統計）を比較し、意図通りのシナリオが
実際に発生したかどうかを判定してください。

回答は以下の JSON 形式で**厳密に**返してください。

```json
{
  "matches_intended_scenario": true,
  "confidence": 0.0,
  "discrepancies": ["観測が意図と食い違う点があれば列挙"],
  "notes": "補足"
}
```

## 意図されたシナリオ

{intended_scenario}

## 観測要約

{observed_summary}
