"""監査隔離の再輸出（実体は :mod:`sbp.core.audit`）。

マスクは層をまたぐ契約であり、``worldmodel``（記号条件付け）と ``reasoning``（記録尤度）の
両方が必須引数として取る。``worldmodel`` は ``reasoning`` を import できないため、実体は
``core`` に置いている。この再輸出は「推論層から見た監査隔離」という読み口を残すためのもので、
新しいコードは :mod:`sbp.core.audit` から直接取ってよい。
"""

from __future__ import annotations

from sbp.core.audit import AuditMask, MaskedEvidence, no_audit

__all__ = ["AuditMask", "MaskedEvidence", "no_audit"]
