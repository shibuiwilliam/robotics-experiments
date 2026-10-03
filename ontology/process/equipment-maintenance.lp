% 設備保全ドメインの事象計算（clingo / ASP）。設計書 §3.8 に対応する。
%
% この記述は三通りにコンパイルされる（設計書 §3.8）:
%   (1) 記号遷移 T — 整合残差 ‖A(f(z,a)) − T(A(z),a)‖ の計算用（このファイルを直接使う）
%   (2) 信念更新エンジンの遷移事前分布 — 半マルコフ滞在時間（delay_* 述語から導く）
%   (3) 事象昇格の逆写像 — フルーエントの変化から起こったはずの事象型を推定する
%
% ローカル名は ontology/domain/equipment-maintenance.ttl と一致させること
% （`make ontology-check` が双方向の対応を検査する）。
% 遅延分布はここでは種別とパラメータの宣言のみを行い、サンプリングは Python 側で実装する
% （ASP は論理の表現に用い、確率計算には用いない）。

% ---- フルーエント型と事象型の宣言 -------------------------------------------

fluent_type(running).
fluent_type(under_maintenance).
fluent_type(faulted).
fluent_type(vibration_anomaly).
fluent_type(accessible).
fluent_type(inspected).
fluent_type(present).

event_type(start_cmd).
event_type(stop_cmd).
event_type(fault).
event_type(maint_start).
event_type(maint_end).
event_type(anomaly_onset).
event_type(anomaly_cleared).
event_type(goods_issue).
event_type(goods_receipt).

% ---- 排他制約（オントロジーの mutuallyExclusiveWith と対応）------------------

exclusive(running, under_maintenance).
exclusive(under_maintenance, running).

% ---- initiates / terminates -------------------------------------------------
% initiates(EventType, FluentType) / terminates(EventType, FluentType)

initiates(start_cmd, running).
terminates(start_cmd, accessible).      % 稼働中は人が近づけない

terminates(stop_cmd, running).
initiates(stop_cmd, accessible).        % 停止すれば近づける

terminates(fault, running).
initiates(fault, faulted).
initiates(fault, accessible).           % 故障停止でも近づけるようになる

initiates(maint_start, under_maintenance).
initiates(maint_start, accessible).
terminates(maint_end, under_maintenance).
terminates(maint_end, faulted).
initiates(maint_end, inspected).

initiates(anomaly_onset, vibration_anomaly).
terminates(anomaly_cleared, vibration_anomaly).

terminates(goods_issue, present).
initiates(goods_receipt, present).

% ---- 発火条件（条件付き initiates）------------------------------------------
% 保全中は起動指令で稼働に入らない（設計書 §3.8 の "if not UnderMaintenance(a)"）。

precondition_absent(start_cmd, running, under_maintenance).

% ---- 遅延分布の宣言 ---------------------------------------------------------
% delay(EventType, FluentType, Distribution, Param1Milli, Param2Milli)
% パラメータは整数で持つ（ASP は実数を扱わないため、ミリ単位の整数で表現する）。
%   lognormal: Param1Milli = mu * 1000（自然対数の平均）, Param2Milli = sigma * 1000
%   exponential: Param1Milli = rate * 1000（1/秒）, Param2Milli = 0

delay(start_cmd, running, lognormal, 3401, 500).      % LogNormal(ln 30s ≈ 3.401, 0.5)
delay(stop_cmd, running, lognormal, 2303, 400).       % LogNormal(ln 10s ≈ 2.303, 0.4)
delay(fault, running, exponential, 200, 0).           % Exponential(1/5s = 0.2)
delay(fault, faulted, exponential, 200, 0).

% ---- 事象計算の中核規則 -----------------------------------------------------
% happens(Event, Entity, Time) と holds_at(Fluent, Entity, Time) を結ぶ。
% 時刻は整数の論理ステップ（実時刻の対応は Python 側が持つ）。

% 事象が発火したら、その時点以降そのフルーエントは成立する（条件を満たす場合のみ）。
initiated(F, E, T) :-
    happens(Ev, E, T),
    initiates(Ev, F),
    not blocked(Ev, F, E, T).

blocked(Ev, F, E, T) :-
    precondition_absent(Ev, F, Blocker),
    holds_at(Blocker, E, T).

terminated(F, E, T) :-
    happens(Ev, E, T),
    terminates(Ev, F).

% フルーエントは初期化されるか、前時刻から継続する（慣性）。
holds_at(F, E, T) :-
    initiated(F, E, T0),
    T0 < T,
    step(T),
    not clipped(F, E, T0, T).

% T0 は initiated、T は step で束縛する。比較だけに現れる変数は接地できない
% （clingo の unsafe variable）——規則を実際に走らせるまで気付けなかった欠陥である。
clipped(F, E, T0, T) :-
    initiated(F, E, T0),
    terminated(F, E, T1),
    step(T),
    T0 < T1,
    T1 < T.

% ---- 整合性の検査 -----------------------------------------------------------
% 排他フルーエントの同時成立は「拒否」ではなく「不一致候補」である。ASP 側では
% 矛盾として落とさず、exclusive_violation として明示的に導出する（設計書 §3.9）。

exclusive_violation(F, G, E, T) :-
    holds_at(F, E, T),
    holds_at(G, E, T),
    exclusive(F, G),
    F < G.

% ---- 事象昇格の逆写像 -------------------------------------------------------
% フルーエントが真→偽に変化したとき、それを引き起こしえた事象型の候補を列挙する。
% 候補間の尤度比較（StopCmd か Fault か）は他プローブと業務文脈を用いて Python 側で行う。

candidate_event(Ev, F, E, T) :-
    event_type(Ev),
    terminates(Ev, F),
    fluent_change(F, E, T, true, false).

candidate_event(Ev, F, E, T) :-
    event_type(Ev),
    initiates(Ev, F),
    fluent_change(F, E, T, false, true).

#show initiated/3.
#show terminated/3.
#show holds_at/3.
#show exclusive_violation/4.
#show candidate_event/4.

% ---- 確信度伝播の宣言（設計書 §8.9）----------------------------------------
% 規則の実行は信念の上で行うため、確信度をどう伝播するかを**規則ごとに宣言する**。
% confidence_rule(RuleName, HeadPredicate, Propagation)
%   min       : 連言（body の全てが要る）→ 最小値。既定。
%   product   : 独立な連言 → 積。body が独立と言える場合のみ。
%   noisy_or  : 選言（複数の導出が同じ結論を支える）→ 1 - Π(1 - c)。

confidence_rule(inertia, holds_at, min).
confidence_rule(exclusive_violation, exclusive_violation, min).
confidence_rule(event_lifting_inverse, candidate_event, noisy_or).

% ---- 導出の根拠（来歴）------------------------------------------------------
% 導出された原子ごとに、それを支えた body の原子を明示する。Python 側はこの support/2 を
% 読んで確信度を伝播する——導出木を推測せず、規則自身が根拠を申告する形にしている。

support(exclusive_violation(F, G, E, T), holds_at(F, E, T)) :-
    exclusive_violation(F, G, E, T).
support(exclusive_violation(F, G, E, T), holds_at(G, E, T)) :-
    exclusive_violation(F, G, E, T).

support(candidate_event(Ev, F, E, T), fluent_change(F, E, T, From, To)) :-
    candidate_event(Ev, F, E, T),
    fluent_change(F, E, T, From, To).

support(holds_at(F, E, T), initiated(F, E, T0)) :-
    holds_at(F, E, T),
    initiated(F, E, T0),
    T0 < T.

#show support/2.
#show confidence_rule/3.
