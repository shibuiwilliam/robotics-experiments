# Robotics Experiments

[English](./README.md)

ロボット、AI エージェント、世界モデル、オントロジー、記憶、業務システムがどのように協調できるかを探る、再現可能な研究プロトタイプ集です。

各プロジェクトには共通する考え方があります。シミュレーションを計測可能な正解データとして活用し、ローカル環境で実行できる規模に保ち、一度きりのデモではなく再実行可能なテストによって主張を検証します。開発とテストの大部分はオフラインで実行でき、クラウドモデルとの連携は任意の機能として明示的なコマンドの背後に分離されています。

> このリポジトリは研究用ワークスペースであり、本番向けロボティクス基盤ではありません。実験は主に Apple Silicon Mac 上の MuJoCo、シミュレーションロボット、合成された業務システムを対象としています。

## プロジェクト一覧

各ディレクトリは、環境、ドキュメント、テスト、実験出力を個別に持つ独立したプロジェクトです。

| プロジェクト | 検証する問い | 最初に読むもの |
|---|---|---|
| [Multi-World Search](./MultiWorldSearch/) | 物理観測、業務レコード、文書、スキルを、身体性を持つエージェント群の共有検索メモリにできるか | [README](./MultiWorldSearch/README.md) · [シナリオ](./MultiWorldSearch/SCENARIOS.md) |
| [Musubi](./Musubi/) | ロボット、業務 AI エージェント、外部システムが、来歴と説明責任を保ちながら一つの共有オントロジーで協調できるか | [README](./Musubi/README.md) · [プロジェクト定義](./Musubi/PROJECT.md) |
| [Physical Semantic Layer](./PhysicalSemanticLayer/) | 異種ロボット、エージェント、クラウドシステムの間で、共通意味層が物理的な意味をどこまで忠実に翻訳できるか | [README](./PhysicalSemanticLayer/README.md) · [シナリオ概要](./PhysicalSemanticLayer/docs/scenarios/overview.md) |
| [RO-SimLab](./RO-SimLab/) | 物理知覚から業務記録との照合、タスク実行、形式検証、書き戻しまでをロボティクスオントロジーで閉ループ化できるか | [README](./RO-SimLab/README.md) · [調査ノート](./RO-SimLab/RESEARCH.md) |
| [Tuned Past Action](./TunedPastAction/) | 過去の行動を現在の場面に合わせて幾何学的に調整することで、VLA は分布シフト下でも安全に経験を再利用できるか | [プロジェクト定義](./TunedPastAction/PROJECT.md) · [実験結果](./TunedPastAction/results/REPORT.md) |
| [GTWM](./gtwm/) | 学習型世界モデルと業務オントロジーを組み合わせ、倉庫業務の接地された予測型データ空間を構成できるか | [PoC 計画](./gtwm/docs/poc_plan.md) · [進捗](./gtwm/docs/status.md) |

### どのプロジェクトから見るべきか

- 検索、共有メモリ、マルチエージェント協調なら **Multi-World Search**
- オントロジー駆動の協調、主張の調停、監査可能な意思決定なら **Musubi**
- 単位、座標系、不確実性、翻訳忠実度、安全ゲートなら **Physical Semantic Layer**
- シミュレーション倉庫と業務記録をつなぐ意味的な閉ループなら **RO-SimLab**
- メモリ拡張 VLA と解釈可能な軌跡調整なら **Tuned Past Action**
- 学習型世界モデル、記号接地、整合性ギャップ、WHAT-IF 予測なら **GTWM**

## はじめ方

### 前提環境

正確な要件はプロジェクトごとに異なりますが、主に次のツールを使います。

- 十分に検証されたローカル実行環境として Apple Silicon 搭載 macOS
- 各 `pyproject.toml` が指定する Python 3.11 または 3.12
- Python 環境と依存関係の管理に [`uv`](https://docs.astral.sh/uv/)
- シミュレーションに MuJoCo（必要なプロジェクトでは Python 依存として導入）
- `make`、または RO-SimLab 用の [`just`](https://just.systems/)
- Elasticsearch、Oxigraph、分散サービスを使う実験に限り Docker

リポジトリをクローンしたら、対象プロジェクトのディレクトリに移動してセットアップしてください。依存パッケージと Python バージョンが意図的に異なるため、リポジトリ直下に共通の仮想環境は作成しません。

```bash
git clone <repository-url>
cd robotics-experiments

# 例: Musubi のオフライン品質ゲートを実行
cd Musubi
make setup
make gen
make check
```

各プロジェクトで最初に使えるコマンドは次のとおりです。

| プロジェクト | セットアップ | オフライン検証またはスモークテスト |
|---|---|---|
| Multi-World Search | `uv sync --extra dev` | `uv run pytest` |
| Musubi | `make setup && make gen` | `make check` |
| Physical Semantic Layer | `make setup` | `make doctor && make check` |
| RO-SimLab | `just sync` | `just check` |
| Tuned Past Action | `uv sync --extra dev` | `uv run pytest -q` |
| GTWM | `make setup` | `make doctor && make test` |

多くの Make ベースのプロジェクトでは `make help`、RO-SimLab では `just --list` で利用できる操作を確認できます。それ以外は、プロジェクトの README または運用ガイドを参照してください。ライブモデル、本実験、対話型ビューアーを動かす前にも各手順を確認してください。API キー、Docker サービス、追加モデルのダウンロード、`mjpython` が必要な場合があります。

## 共通する研究テーマ

検証する仮説はそれぞれ異なりますが、リポジトリ全体に次の考え方が流れています。

- **正解データとしてのシミュレーション:** MuJoCo の制御された物理環境と真値を定量評価に利用します。
- **異なる世界をつなぐ意味:** オントロジーと型付き中間表現により、ロボット状態を文書、計画、業務レコードへ接続します。
- **来歴と不確実性:** 観測や判断について、出所と信頼度を説明できるようにします。
- **オフライン優先の再現性:** シード固定、ローカルモック、記録済みモデル応答、機械採点可能な基準を活用します。
- **明確な境界:** 実機制御、本番規模への拡張、実験で裏付けられない主張は対象外として明示します。

## このリポジトリで作業する方へ

変更を始める前に、対象プロジェクト内のドキュメントを確認してください。多くのプロジェクトは、次のように文書を使い分けています。

- `PROJECT.md` または PoC 計画: 研究課題、スコープ、成功基準
- `README.md`: セットアップと日常的な入口
- `CLAUDE.md`: 詳細な開発規約とアーキテクチャ制約
- `SCENARIOS.md`、`docs/`、`experiments/`: 評価プロトコル
- `runs/`、`results/`、`scoreboard/`: 生成された根拠データとレポート

意図的な横断作業でない限り変更は一つのプロジェクトに限定し、決定的なシードと真値の隔離を維持してください。また、`.env` の秘密情報は決してコミットしないでください。検証方法は各プロジェクトが定める品質ゲートを正とします。

## ライセンス

このリポジトリは [MIT License](./LICENSE) の下で公開されています。
