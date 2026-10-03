"""SBP の例外階層。

すべての例外は :class:`SbpError` を根とする。CLAUDE.md の規約により例外は握りつぶさず、
リトライは eventlog の consumer 層と ``llm.Provider`` の 2 箇所だけで行う。

不変条件の違反を表す例外は :class:`InvariantViolation` の下位に置く。これらは「呼び出し側が
回復を試みるべきでない設計上の誤り」であり、捕捉してフォールバックするのではなく、
修正されるべきバグとして扱う。
"""

from __future__ import annotations


class SbpError(Exception):
    """SBP が送出するすべての例外の基底。"""


# ---- 設定・起動 --------------------------------------------------------------


class ConfigError(SbpError):
    """設定値の欠落・矛盾。"""


class DependencyUnavailableError(SbpError):
    """依存サービス（Redpanda / Postgres / Oxigraph / MinIO）に到達できない。"""


# ---- 不変条件の違反 ----------------------------------------------------------


class InvariantViolation(SbpError, ValueError):
    """CLAUDE.md の不変条件 1〜12 に対する違反。設計上の誤りであり回復を試みない。

    ``ValueError`` も継承する。pydantic のバリデータは ``ValueError`` だけを
    ``ValidationError`` に包むため、この継承によって

    - 直接呼び出し（``Interval(...)``, ``validate_iri(...)``）では具体的な SBP 例外が飛び、
    - pydantic モデルの構築時には他の検証エラーと同じ ``ValidationError`` にまとまる

    の両方が成立する。呼び出し側は文脈に応じてどちらでも捕捉できる。
    """


class AssertionIncompleteError(InvariantViolation):
    """アサーションに必須フィールドが欠けている（不変条件 1）。"""


class AppendOnlyViolation(InvariantViolation):
    """アサーションの UPDATE / DELETE を試みた（不変条件 2）。"""


class ModalityError(InvariantViolation):
    """様相の不正。推定出力を ``observed`` にする、``hypothetical`` を主信念に入れる等。

    不変条件 3 に対応する。
    """


class IdempotencyError(InvariantViolation):
    """``event_id`` による冪等性が破れている（不変条件 4）。"""


class VocabularyError(InvariantViolation):
    """IRI の直書き、または ``sbp.core.vocab`` に無い語彙の参照（不変条件 5）。"""


class AuditIsolationError(InvariantViolation):
    """監査対象の記録または派生信念が推定の入力に混入した（不変条件 6）。"""


class CalibrationError(InvariantViolation):
    """較正されていない生スコアを ``belief`` に入れようとした（不変条件 7）。"""


class TemporalError(InvariantViolation):
    """naive datetime、閉区間、負の期間など時間表現の不正（不変条件 8）。"""


class RawRetentionError(InvariantViolation):
    """生映像・音声の永続化を試みた（不変条件 9）。"""


class OracleAccessError(InvariantViolation):
    """``sbp.testing`` 以外から真値（オラクル）を読もうとした（不変条件 10）。"""


class LlmHotPathError(InvariantViolation):
    """信念更新・照合・事象昇格のホットパスから LLM を呼んだ（不変条件 11）。"""


class SecretLeakError(InvariantViolation):
    """シークレットがコード・ログ・fixture に出ようとした（不変条件 12）。"""


# ---- オントロジー ------------------------------------------------------------


class OntologyError(SbpError):
    """オントロジーの構文・制約・版に関する誤り。"""


class ShapeViolation(OntologyError):
    """SHACL の ``sh:Violation``（構造制約違反）。登録を拒否し隔離キューへ回す。"""


class OntologyVersionError(OntologyError):
    """オントロジー版の非互換、または版の欠落。"""


# ---- イベントログ ------------------------------------------------------------


class EventLogError(SbpError):
    """イベントログ（Redpanda）に関する誤り。"""


class SchemaValidationError(EventLogError):
    """イベントが ``schemas/*.json`` に適合しない。"""


class SchemaVersionError(EventLogError):
    """``schema_version`` の非互換（後方互換のみ許可）。"""


# ---- 知識層 ------------------------------------------------------------------


class KnowledgeError(SbpError):
    """知識グラフ・潜在索引に関する誤り。"""


class AssertionNotFoundError(KnowledgeError):
    """指定したアサーションが存在しない（または as-of 時点で有効でない）。"""


class RegroundingError(KnowledgeError):
    """再接地ジョブの失敗。"""


class ArtifactNotFoundError(KnowledgeError):
    """モデル成果物（重み・プローブ束ね）が置き場に無い。"""


class ArtifactImmutableError(KnowledgeError):
    """既存のモデル成果物を上書きしようとした。

    成果物は版ごとに不変である。同じキーの中身が入れ替わると、``latest`` を禁じていても
    実質 ``latest`` になり、過去の信念の説明が再構成できなくなる（設計書 §8.8）。
    """


# ---- 業務統合 ----------------------------------------------------------------


class IntegrationError(SbpError):
    """業務システム連携に関する誤り。"""


class MappingError(IntegrationError):
    """写像（RML/R2RML）の不変条件違反。"""


class UnresolvedReferenceError(IntegrationError):
    """``is_about`` の対象実体を解決できない。記録は捨てず ``UnresolvedRef`` として残す。"""


class WritebackError(IntegrationError):
    """業務システムへの書き戻しの失敗。補償アクションの対象。"""


# ---- ワールドモデル ----------------------------------------------------------


class WorldModelError(SbpError):
    """WM の推論・学習・レジストリに関する誤り。"""


class ModelVersionError(WorldModelError):
    """モデル版の指定漏れ（``latest`` 禁止）または互換性の欠如。"""


# ---- 接地層 ------------------------------------------------------------------


class BridgeError(SbpError):
    """接地層（プローブ・昇格・束縛・較正）に関する誤り。"""


class ProbePromotionError(BridgeError):
    """昇格ゲート（AUROC / ECE / 整合残差の AND）を満たさないプローブの ``active`` 化。"""


class IdentityBindingError(BridgeError):
    """同一性束縛の失敗・競合。"""


# ---- 推論層 ------------------------------------------------------------------


class ReasoningError(SbpError):
    """推論・照合・予測に関する誤り。"""


class DiscrepancyTransitionError(ReasoningError):
    """``Discrepancy`` の状態機械で許されない遷移。"""


class RuleError(ReasoningError):
    """規則（Datalog / ASP / SHACL rules）の実行失敗。"""


# ---- アクション層 ------------------------------------------------------------


class ActionError(SbpError):
    """アクションの定義・検証・実行に関する誤り。"""


class PreconditionNotMetError(ActionError):
    """意味契約の前提（記号・物理・規範）を満たさない。"""


class ApprovalRequiredError(ActionError):
    """``riskClass`` と深刻度に対して必要な承認が得られていない。"""


class CompensationMissingError(ActionError):
    """補償アクションが定義されていない ActionType の登録。"""


# ---- LLM ---------------------------------------------------------------------


class LlmError(SbpError):
    """LLM プロバイダに関する誤り。"""


class FixtureMissingError(LlmError):
    """``SBP_LLM_MODE=replay`` で fixture が存在しない。黙って live に落ちず失敗させる。"""


class StructuredOutputError(LlmError):
    """LLM の構造化出力が pydantic または SHACL の検証を通らない。"""
