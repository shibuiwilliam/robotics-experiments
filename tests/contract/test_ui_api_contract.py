"""UI と API の契約が一致していることの契約テスト（M8-16）。

**なぜ必要になったか。** M7-04 の UI は API の形を**手で写した型**で書かれており（ADR-0008 で
GraphQL のコード生成を入れない決定）、API 側とずれても誰も気付かなかった。M8-16 で突き合わせた
ところ、5 箇所ずれていた（``docs/PROGRESS.md`` 所見 83）:

- 類型に ``QUANTITY`` という**存在しない値**があり、実在する ``NORM``（規範）が無かった
- 状態が大文字（``OPEN``）で、API は ``Open`` を返す
- 解決結果が UI 独自の 5 値で、**API が受け付けない**（``extra="forbid"``）
- 証拠が ``Belief[]`` 埋め込みで、そんな経路は存在しなかった
- 主体が単数で、API は複数返す

UI 側のテストは全部通っていた——**自分で決めた型に対して自分で作った fixture を当てていた**ので、
API と合っているかは一度も検査されていなかった。ここがその検査である。

**TypeScript を解析しない。** 正規表現で値の列挙を抜き出すだけにしてある。構文解析を入れると
tsc への依存が `make check` に入り、「追加のインストール無しで必ず走る」という方針が崩れる。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sbp.api.commands import CurationCommand, ResolutionCommand
from sbp.api.schema import ConceptProposalType, DiscrepancyType, ExpectationType
from sbp.reasoning.discovery import ProposalStatus
from sbp.reasoning.expectation import ExpectationStatus
from sbp.reasoning.reconciliation import (
    DiscrepancyKind,
    DiscrepancyStatus,
    ResolutionOutcome,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_TYPES = REPO_ROOT / "apps/ui/src/lib/types.ts"
UI_API = REPO_ROOT / "apps/ui/src/lib/api.ts"


def _source(path: Path) -> str:
    """UI の原文を読む。

    Args:
        path: 読むファイル。
    """
    if not path.is_file():
        pytest.skip(f"{path} が無い（UI を持たない構成）")
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    """TypeScript のコメントを落とす。

    **原文を正規表現で探すときに要る。** 説明文に書いた識別子を「コードが使っている」と
    誤認すると、説明を書いたせいでテストが落ちる（所見 87）。

    Args:
        source: TypeScript の原文。
    """
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", without_block)


def _string_union(source: str, name: str) -> set[str]:
    """``export type <name> = "a" | "b"`` の値を抜き出す。

    Args:
        source: TypeScript の原文。
        name: 型の名前。
    """
    match = re.search(rf"export type {name} =(.*?);", source, re.DOTALL)
    assert match is not None, f"{name} が UI に無い"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_the_discrepancy_state_values_match_the_backend() -> None:
    """UI の状態の値が ``DiscrepancyStatus`` と一致する。

    大文字の別名を持つと変換が必要になり、変換を忘れた経路で一致判定が静かに外れる。
    """
    found = _string_union(_source(UI_TYPES), "DiscrepancyState")

    assert found == {status.value for status in DiscrepancyStatus}


def test_the_resolution_outcomes_match_the_backend() -> None:
    """UI の解決結果が ``ResolutionOutcome`` と一致する。

    **ずれていると API が弾く**（``extra="forbid"`` と ``Literal``）。以前は UI 独自の 5 値
    （``RECORD_WRONG`` など）で、投入が 400 になる形だった。
    """
    found = _string_union(_source(UI_TYPES), "ResolutionOutcome")

    assert found == {outcome.value for outcome in ResolutionOutcome}


def test_the_discrepancy_kinds_match_the_backend() -> None:
    """UI の類型の番号と表示名が五類型をすべて覆う。

    以前は ``QUANTITY`` という存在しない類型があり、実在する ``NORM`` が無かった。
    """
    source = _source(UI_TYPES)
    match = re.search(r"export type DiscrepancyKind =(.*?);", source, re.DOTALL)
    assert match is not None
    numbers = {int(value) for value in re.findall(r"\b(\d)\b", match.group(1))}

    assert numbers == {int(kind) for kind in DiscrepancyKind}

    labels = re.search(r"DISCREPANCY_KIND_LABEL[^=]*=\s*\{(.*?)\};", source, re.DOTALL)
    assert labels is not None
    labelled = {int(key) for key in re.findall(r"(\d):", labels.group(1))}
    assert labelled == numbers, "番号に表示名が無いものがある"


def test_the_queried_discrepancy_fields_exist_on_the_graphql_type() -> None:
    """UI が要求するフィールドが GraphQL の型に実在する。

    **存在しないフィールドを要求すると GraphQL がエラーを返す**ので実行時には分かるが、
    その時点では「UI が壊れている」としか分からない。ここで落ちれば原因が分かる。
    """
    source = _source(UI_API)
    match = re.search(r"const DISCREPANCY_FIELDS = `(.*?)`", source, re.DOTALL)
    assert match is not None, "DISCREPANCY_FIELDS が UI に無い"
    requested = set(match.group(1).split())
    available = set(DiscrepancyType.__annotations__)

    missing = requested - available
    assert not missing, f"UI が要求するフィールドが GraphQL の型に無い: {sorted(missing)}"


def test_the_resolution_command_payload_matches_the_contract() -> None:
    """UI が送るキーが ``ResolutionCommand`` のフィールドと一致する。

    ``extra="forbid"`` なので、余分なキーも足りないキーも 400 になる。以前は camelCase を
    送っており、**この形のまま出荷されていた**。
    """
    source = _source(UI_API)
    match = re.search(
        r"/commands/resolve_discrepancy.*?JSON\.stringify\(\{(.*?)\}\)",
        source,
        re.DOTALL,
    )
    assert match is not None, "解決の投入が UI に無い"
    sent = set(re.findall(r"^\s*([a-z_]+):", match.group(1), re.MULTILINE))
    required = {
        name for name, field in ResolutionCommand.model_fields.items() if field.is_required()
    }

    assert required <= sent, f"必須のキーが送られていない: {sorted(required - sent)}"
    assert sent <= set(ResolutionCommand.model_fields), (
        f"契約に無いキーを送っている: {sorted(sent - set(ResolutionCommand.model_fields))}"
    )


def test_the_curation_command_payload_matches_the_contract() -> None:
    """概念採否の投入も契約と一致する。"""
    source = _source(UI_API)
    match = re.search(r"/commands/curate_concept.*?JSON\.stringify\(\{(.*?)\}\)", source, re.DOTALL)
    assert match is not None, "概念採否の投入が UI に無い"
    sent = set(re.findall(r"^\s*([a-z_]+):", match.group(1), re.MULTILINE))
    required = {name for name, field in CurationCommand.model_fields.items() if field.is_required()}

    assert required <= sent, f"必須のキーが送られていない: {sorted(required - sent)}"
    assert sent <= set(CurationCommand.model_fields)


def test_the_ui_does_not_read_discrepancies_from_the_stream() -> None:
    """UI が不一致をイベントストリームから読んでいない。

    起票イベントは**通知**であって不一致そのものではない（所見 62）。ストリームを読む形に
    戻ると、「`payload` が `Discrepancy` である」という成立しない前提に戻る。
    """
    source = _source(UI_API)
    match = re.search(r"export async function fetchDiscrepancies.*?\n\}", source, re.DOTALL)
    assert match is not None

    assert "/stream/" not in match.group(0), "不一致をストリームから読んでいる"
    assert "discrepancies(" in match.group(0), "GraphQL の資源を引いていない"


def test_the_topics_the_ui_reads_exist() -> None:
    """UI が名指しするトピックが ``scripts/topics.py`` に実在する。

    **存在しないトピックは空を返す。** `/stream/{topic}` は名前を受け取って読むだけなので、
    綴り違いや拠点違いは「イベントが無い」として表示される——`0 件` と `トピックが無い` が
    区別できない（所見 32 / 85）。

    UI が読んでいたのは `central.reasoning.expectation` と `central.reasoning.concept` で、
    **どちらも存在しなかった**（期待は拠点ごと、概念提案はトピック自体が無い）。
    """
    from scripts.topics import central_topics, topics_for_site  # noqa: PLC0415

    declared = {spec.name for spec in central_topics()}
    # 拠点部分は実行時に決まるので、`<layer>.<class>` の部分で比べる
    probe_site = "siteContractProbe"
    site_classes = {
        spec.name.removeprefix(f"{probe_site}.") for spec in topics_for_site(probe_site)
    }

    # **コメントを落としてから探す。** 落とさないと、説明文に書いた「以前は
    # `central.reasoning.expectation` を読んでいた」というコメントを拾って落ちる
    # （実際に落ちた。所見 87）
    source = _strip_comments(_source(UI_API))
    literals = set(re.findall(r'[`"]([a-z]+\.[a-z]+\.[a-z]+)[`"]', source))
    templated = set(re.findall(r"\$\{siteId\}\.([a-z]+\.[a-z]+)", source))

    for topic in literals:
        assert topic in declared, (
            f"UI が読む {topic} が scripts/topics.py に無い（存在しないトピックは空を返す）"
        )
    for suffix in templated:
        assert suffix in site_classes, f"UI が読む <site>.{suffix} が scripts/topics.py に無い"


def test_a_failed_read_is_not_rendered_as_an_empty_result() -> None:
    """読めなかったことを空として描かない（所見 32）。

    **「提案が無い」と「発見ループが動いていない」は違う。** M8-19 で `fetchProposals` が
    GraphQL に繋がったので、以前の「未配線だから例外」という形は無くなった——残るべき保証は
    **失敗が空に化けないこと**である。

    2 点を検査する: (1) GraphQL の応答に `errors` があれば投げる、(2) 画面が提案の読み出しの
    失敗を独立した状態として持つ（他のビューを落とさず、理由を出す）。
    """
    api = _strip_comments(_source(UI_API))
    assert re.search(r"payload\.errors[\s\S]{0,120}throw new ApiError", api), (
        "GraphQL の errors を投げていない（空の成功として描かれる）"
    )

    app = _strip_comments(_source(REPO_ROOT / "apps/ui/src/App.tsx"))
    assert "proposalError" in app, "提案の読み出しの失敗を画面が区別していない"
    assert "setProposals([])" in app, "失敗時に一覧を空に戻していない（古い内容が残る）"


def test_the_expectation_status_values_match_the_backend() -> None:
    """UI の期待の状態が ``ExpectationStatus`` と一致する（M8-18）。

    以前は大文字の別名（``ACTIVE``）で、API が返す ``Active`` と一致していなかった（所見 86）。
    """
    found = _string_union(_source(UI_TYPES), "ExpectationStatus")

    assert found == {status.value for status in ExpectationStatus}


def test_the_queried_expectation_fields_exist_on_the_graphql_type() -> None:
    """UI が要求する期待のフィールドが GraphQL の型に実在する。"""
    source = _source(UI_API)
    match = re.search(r"const EXPECTATION_FIELDS = `(.*?)`", source, re.DOTALL)
    assert match is not None, "EXPECTATION_FIELDS が UI に無い"
    requested = set(match.group(1).split())
    available = set(ExpectationType.__annotations__)

    missing = requested - available
    assert not missing, f"UI が要求するフィールドが GraphQL の型に無い: {sorted(missing)}"


def test_the_ui_does_not_read_expectations_from_the_stream() -> None:
    """UI が期待をイベントストリームから読んでいない（M8-18）。

    期待の全体はイベントの封筒に入らない（``expected_effects`` / ``verification`` /
    ``history`` を入れる場所が無い。所見 62 / 86）。
    """
    source = _strip_comments(_source(UI_API))
    match = re.search(r"export async function fetchExpectations.*?\n\}", source, re.DOTALL)
    assert match is not None

    assert "/stream/" not in match.group(0), "期待をストリームから読んでいる"
    assert "expectations(" in match.group(0), "GraphQL の資源を引いていない"


def test_the_proposal_status_values_match_the_backend() -> None:
    """UI の提案の採否が ``ProposalStatus`` と一致する（M8-19）。

    **小文字である。** backend の ``StrEnum`` の値で、不一致（所見 83）と期待（所見 86）では
    UI が大文字の別名を持っていて API と一致していなかった。
    """
    found = _string_union(_source(UI_TYPES), "ProposalStatus")

    assert found == {status.value for status in ProposalStatus}


def test_the_queried_proposal_fields_exist_on_the_graphql_type() -> None:
    """UI が要求する提案のフィールドが GraphQL の型に実在する。

    以前の UI は ``label`` / ``rationale`` という**backend に無い名前**を使っていた（所見 85）。
    """
    source = _source(UI_API)
    match = re.search(r"const PROPOSAL_FIELDS = `(.*?)`", source, re.DOTALL)
    assert match is not None, "PROPOSAL_FIELDS が UI に無い"
    requested = set(match.group(1).split())
    available = set(ConceptProposalType.__annotations__)

    missing = requested - available
    assert not missing, f"UI が要求するフィールドが GraphQL の型に無い: {sorted(missing)}"


def test_the_ui_does_not_read_proposals_from_the_stream() -> None:
    """UI が提案をイベントストリームから読んでいない（M8-19）。

    **以前は存在しないトピックを読んでいた**（`central.reasoning.concept`。所見 85）
    ——トピックも API 資源も無く、読めば必ず失敗する形で出荷されていた。
    """
    source = _strip_comments(_source(UI_API))
    match = re.search(r"export async function fetchProposals.*?\n\}", source, re.DOTALL)
    assert match is not None

    assert "/stream/" not in match.group(0), "提案をストリームから読んでいる"
    assert "proposals(" in match.group(0), "GraphQL の資源を引いていない"


def test_the_ui_reads_no_stream_at_all() -> None:
    """**UI にストリームの読み出しが 1 つも残っていない**（M8-18 / M8-19）。

    4 ビューすべてが GraphQL の資源を読む。`/stream/{topic}` の購読方式は未決のまま
    （ADR-0008 の未決）で、いまは消費者が 1 人も居ない——残っていると「動くはず」と読まれる。
    """
    source = _strip_comments(_source(UI_API))

    assert "/stream/" not in source, "UI がまだストリームを読んでいる"
