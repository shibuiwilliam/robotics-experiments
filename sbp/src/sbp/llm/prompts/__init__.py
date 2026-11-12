"""プロンプトの版管理（LLM 利用ポリシー、M6-03）。

プロンプトは ``prompts/<task>/<version>.md``。**変更は版を上げる**。本文のハッシュを来歴に残すので、
版を据え置いて本文を書き換えると「同じ版で違う挙動」になり、過去の出力を再現できなくなる。
"""

from __future__ import annotations

__all__: list[str] = []
