# 概念命名（タスク3：概念候補の命名と説明）

あなたは物流倉庫の知識工学の専門家です。以下は、世界モデルの予測残差をクラスタリングして
得られた「既存のオントロジー記号（gt: 名前空間のクラス）では説明できない」クラスタの要約です。

このクラスタに対応する可能性のある未知概念について、以下の JSON 形式で**厳密に**回答してください。
説明文以外のテキスト（前置き・後書き）を含めないこと。

```json
{
  "proposed_label_ja": "候補名（日本語）",
  "proposed_label_en": "candidate name (English)",
  "definition": "この概念が何を指すかの定義文（1-2文）",
  "relation_to_existing_classes": "既存クラス（gt:Pallet, gt:Case, gt:Vehicle, gt:Worker, gt:Zone, gt:Slot, gt:Equipment 等）との関係",
  "evidence_summary": "根拠となった残差クラスタの特徴の要約"
}
```

## クラスタ情報

- cluster_id: {cluster_id}
- n_members: {n_members}
- feature_summary: {feature_summary}
