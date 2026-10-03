% 設備保全ドメインの規範規則（clingo / ASP）。設計書 §7.4、§8.9 に対応する。
%
% 規範は**出典を持つ**（設計書 §3.3: 根拠文書まで 3 ホップ以内で遡れなければ監査に使えない）。
% そのため各規則は norm_rule/4 で文書・節・重大度を申告する。Python 側はこれを
% sbp.core.records.Norm に突き合わせる（規則だけがあって Norm が無い、またはその逆を検出する）。
%
% 規範違反の判定は**信念の上**で行う。確信度 θ_5 未満の信念では起票しない——その閾値は
% Settings（運用で動かす値）であり、ここには書かない。

% ---- 規則の申告 -------------------------------------------------------------
% norm_rule(RuleName, SourceDocument, SourceClause, Severity)

norm_rule(inspection_before_restart, "maintenance-standard", "4.2", important).
norm_rule(no_access_while_running, "safety-standard", "2.1", critical).

confidence_rule(inspection_before_restart, norm_violation, min).
confidence_rule(no_access_while_running, norm_violation, min).

% ---- 規範 1: 保全完了後は点検済みでなければ稼働させない ----------------------
% 「保全したが点検記録が無いまま動き出した」状況。CMMS の作業指示が完了になっていても
% 点検フルーエントが立っていなければ違反である。

norm_violation(inspection_before_restart, E, T) :-
    holds_at(running, E, T),
    maintenance_completed(E, T0),
    T0 < T,
    step(T),
    not holds_at(inspected, E, T).

support(norm_violation(inspection_before_restart, E, T), holds_at(running, E, T)) :-
    norm_violation(inspection_before_restart, E, T).

% ---- 規範 2: 稼働中に人が立ち入れる状態にしてはならない ----------------------
% accessible は「人が近づける」ことを表す。稼働中に成立していれば安全規程違反である
% （オントロジーの initiates / terminates では排他にしていない——物理的には起こりうる
% から検出対象になる。排他にすると「起こり得ない」ことになって検出できない）。

norm_violation(no_access_while_running, E, T) :-
    holds_at(running, E, T),
    holds_at(accessible, E, T).

support(norm_violation(no_access_while_running, E, T), holds_at(running, E, T)) :-
    norm_violation(no_access_while_running, E, T).
support(norm_violation(no_access_while_running, E, T), holds_at(accessible, E, T)) :-
    norm_violation(no_access_while_running, E, T).

#show norm_violation/3.
#show norm_rule/4.
