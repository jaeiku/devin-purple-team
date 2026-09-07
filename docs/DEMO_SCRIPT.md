# Demo script — Devin Purple Team (≤ 5 min Loom)

Audience: VP of Engineering + senior ICs who are curious about Devin.
Structure: **What → How → Why → When**. Target ~4:30 so there is slack.

Screens to have open before recording (in tab order):

1. Red team console (`:8001`)
2. GitHub fork issues filtered to `label:red-team`
   (<https://github.com/jaeiku/superset/issues?q=label%3Ared-team>)
3. A Devin session page (any previous remediation session)
4. Blue Team SOC dashboard (`:8003`) — Operations tab
5. Same dashboard — Leadership tab
6. Editor with `blue-team/app/guardrails.py`, `blue-team/app/poller.py`

Tip: inject **before** you start recording (a fix PR takes 2–5 minutes), then
inject one more live on camera so the audience sees the trigger fire. That way
one finding is already at `pr_open`/`merged` while another is `investigating`.

---

## English

### 0:00 — What (problem framing) · ~60 s

> 2025 was the worst year on record for Korean telecom security. All three
> major carriers had subscriber data breaches, record fines, and a lasting hit
> to their reputation.
>
> The scale is what makes this different from most markets. Three operators
> cover roughly 50 million people, so one breach is easily ten million
> customers. And because identity in Korea is centrally keyed on the resident
> registration number plus the phone number, a PII leak doesn't stop at
> "someone knows my address" — it cascades into credit fraud and payment
> takeover.
>
> So imagine an operator that runs Apache Superset as its security-monitoring
> dashboard. When a vulnerability is found in that stack — by a scanner, a
> pentester, an internal red team — today that becomes a ticket, and a human
> on-call engineer picks it up hours or days later.
>
> The workflow problem I'm solving: **shrink "vulnerability filed" to "fix PR
> ready for review" from days to minutes, 24/7, without adding headcount** —
> and make that loop observable enough that an engineering leader trusts it.

### 1:00 — How (live demo + architecture) · ~150 s

*Tab 1 — Red team console.*

> Three components, modelled on how operator security teams are already
> organised. The **red team** is a scripted adversary. This catalogue has eight
> telco vulnerability *types* — MD5-hashed subscriber PII, insecure CDR
> deserialization, hard-coded OSS/BSS credentials, and so on. Every Inject
> click plants a deterministic synthetic file on a new branch in the Superset
> fork and files a GitHub issue — same type, new run number, new issue.

*Click Inject on one type.*

> That's it for the red team. It does **not** call anything downstream.

*Tab 2 — GitHub issues.*

> The **GitHub issue is the event**. It's labelled, assigned, and it now exists
> exactly as it would if a scanner had filed it.

*Tab 4 — Dashboard Operations, watch the feed row appear.*

> The **blue team** watches the fork for open `red-team` issues every fifteen
> seconds — a GitHub Actions workflow and a raw webhook endpoint are also wired
> in, and all three paths collapse onto one idempotency key per issue, so
> duplicate deliveries never create duplicate sessions.
>
> Before it spends anything, it runs guardrails: one session per issue, at most
> two concurrent sessions, a per-session ACU cap that's passed to Devin as
> `max_acu_limit`, and a global ceiling that's enforced by *reserving* the cap
> up front — so the budget is hard, not observational. Then it calls
> `POST /v1/sessions` on the Devin API.

*Tab 3 — Devin session page.*

> Behind the scenes Devin gets the issue, the file path, and a brief that says
> "fix this, open a PR that closes the issue". You can see it clone the fork,
> read the vulnerable module, write the fix, run checks, and open the PR. Here
> it replaced MD5 masking with a peppered HMAC-SHA256.

*Back to Tab 4.*

> The blue team polls the session and then the PR itself. Each row shows a
> unified remediation stage — `queued → investigating → pr_open → merged` —
> derived from two raw states you can also see: the Devin session status and
> the PR state. A finding is only counted as **remediated** when the PR is
> actually merged. Merge stays a human decision.

*Tab 6 — code, briefly.*

> Three architectural decisions worth calling out. First, the trigger is
> GitHub, not the red team — so any scanner or human that files an issue gets
> the same treatment. Second, guardrails live in the blue team backed by a
> shared Postgres store, not in Devin — budget is a platform decision. Third,
> everything is Dockerized with a demo mode that runs the full lifecycle with
> zero credentials, so the workflow can be simulated in CI on every PR.

### 3:30 — Observability (fold into How) · ~30 s

*Tab 5 — Leadership tab.*

> This is the **Blue Team SOC** — the defender's console. "If I were an
> engineering leader, how would I know this is working?" This tab answers
> that in one sentence: a verdict — healthy, in progress, attention, budget
> blocked — plus detection-to-PR coverage, median time to PR, mean ACU per
> remediation, and budget consumed. Every service also emits
> single-line JSON events, so this plugs into an existing SIEM.

### 4:00 — Why Devin · ~35 s

> Why an autonomous agent rather than a script or a scanner with auto-fix?
> Because these fixes aren't templated. Replacing a hashing scheme, removing a
> pickle load from an ingestion path, bumping a pinned dependency and fixing
> what breaks — each needs someone to read the surrounding code, decide, and
> produce a reviewable PR with a rationale. A rules engine can't do that; a
> human can't do it at 3 a.m. for every finding. Devin sits exactly in that
> gap, and the API lets me put it behind guardrails and treat it like any
> other service in the pipeline.

### 4:35 — When (next steps) · ~25 s

> Today this covers one domain — vulnerabilities in a monitoring stack. In a
> real engagement I'd extend it in three directions. Closing the loop into a
> true purple team — today red and blue share a target and telemetry, but
> nothing flows back: fix outcomes should feed the red team's scenarios and
> the detection rules, so every remediation makes the next attack harder to
> land. Widening the surface: the same trigger-guardrail-remediate pattern
> applies to network equipment security baselines, legacy customer-data
> systems, and compatibility and vulnerability management across the
> operator's estate. And hardening for production: real scanner integration,
> usage telemetry from the consumption API, and policy-based auto-merge for
> low-risk classes.

---

## 한국어

### 0:00 — What (문제 정의) · ~60초

> 2025년은 한국 통신사 보안에 있어 최악의 해로 기록될 겁니다. 메이저 3사가
> 모두 개인정보 유출 사태를 겪었고, 사상 최대 규모의 과징금과 함께 회복이
> 어려운 신뢰 손상을 입었습니다.
>
> 다른 시장과 다른 점은 규모입니다. 3개 사업자가 약 5천만 명을 나눠 갖는
> 구조라, 한 번의 유출이 곧 1천만 명 이상의 고객 데이터를 의미합니다. 게다가
> 한국은 주민등록번호 + 휴대폰 번호로 신원을 중앙 관리하기 때문에, 개인정보
> 유출이 "주소가 알려졌다"에서 끝나지 않고 신용정보·결제수단 도용 같은 2차
> 피해로 확산됩니다.
>
> 어떤 통신사가 Apache Superset을 보안 모니터링 대시보드로 운영한다고
> 가정해 봅시다. 스캐너, 침투테스터, 내부 레드팀이 그 스택에서 취약점을
> 찾으면, 오늘은 그게 티켓이 되고, 온콜 엔지니어가 몇 시간 혹은 며칠 뒤에
> 집어 듭니다.
>
> 제가 풀려는 워크플로우 문제는 이겁니다. **"취약점 접수"에서 "리뷰 가능한
> 수정 PR"까지를 며칠에서 몇 분으로, 24시간, 인력 추가 없이** — 그리고 그
> 루프를 엔지니어링 리더가 신뢰할 수 있을 만큼 관측 가능하게 만드는 것.

### 1:00 — How (라이브 데모 + 아키텍처) · ~150초

*탭 1 — 레드팀 콘솔.*

> 통신사 보안 조직이 실제로 나뉘는 방식 그대로 세 컴포넌트입니다.
> **레드팀**은 스크립트화된 공격자예요. 이 카탈로그에는 통신사 취약점
> *유형* 8개가 있습니다 — MD5로 해싱된 가입자 PII, CDR 역직렬화, 하드코딩된
> OSS/BSS 크리덴셜 등. Inject를 누를 때마다 Superset fork의 새 브랜치에
> 결정론적인 합성 파일을 심고 GitHub 이슈를 하나 올립니다 — 같은 유형이라도
> 새 run 번호, 새 이슈입니다.

*유형 하나 Inject 클릭.*

> 레드팀의 역할은 여기서 끝입니다. 다운스트림을 **직접 호출하지 않습니다.**

*탭 2 — GitHub 이슈.*

> **GitHub 이슈 자체가 이벤트**입니다. 라벨과 담당자가 붙어 있고, 스캐너가
> 올렸다면 생겼을 모습 그대로 존재합니다.

*탭 4 — 대시보드 Operations, 피드에 행이 생기는 것을 보여줌.*

> **블루팀**은 15초마다 fork의 open `red-team` 이슈를 감시합니다. GitHub
> Actions 워크플로와 webhook 엔드포인트도 붙어 있고, 세 경로 모두 이슈당
> 하나의 idempotency key로 합쳐지기 때문에 중복 전달이 중복 세션을 만들지
> 않습니다.
>
> 비용을 쓰기 전에 가드레일을 통과합니다. 이슈당 세션 하나, 동시 최대 2개,
> Devin에 `max_acu_limit`으로 넘겨지는 세션당 ACU 캡, 그리고 캡을 *선점*하는
> 방식으로 강제되는 전체 상한 — 그래서 예산은 관측치가 아니라 하드 리밋입니다.
> 그 다음 Devin API의 `POST /v1/sessions`를 호출합니다.

*탭 3 — Devin 세션 페이지.*

> 뒤에서 Devin은 이슈, 파일 경로, 그리고 "이걸 고치고 이슈를 닫는 PR을
> 열어라"는 브리프를 받습니다. fork를 클론하고, 취약한 모듈을 읽고, 수정을
> 쓰고, 검사를 돌리고, PR을 여는 과정이 보입니다. 여기선 MD5 마스킹을
> pepper가 들어간 HMAC-SHA256으로 교체했습니다.

*탭 4로 복귀.*

> 블루팀은 세션을 폴링하고, 그 다음엔 PR 자체를 폴링합니다. 각 행은 통합
> 단계 — `queued → investigating → pr_open → merged` — 를 보여주는데, 그
> 아래 두 원시 상태(Devin 세션 상태, PR 상태)에서 도출됩니다. PR이 실제로
> 머지된 경우에만 **remediated**로 집계합니다. 머지는 사람의 결정으로 남겨
> 뒀습니다.

*탭 6 — 코드, 짧게.*

> 아키텍처 결정 세 가지만 짚겠습니다. 첫째, 트리거는 레드팀이 아니라
> GitHub입니다 — 어떤 스캐너든 사람이든 이슈를 올리면 동일하게 처리됩니다.
> 둘째, 가드레일은 Devin이 아니라 공유 Postgres를 기반으로 블루팀 안에
> 있습니다 — 예산은 플랫폼의 결정이어야 하니까요. 셋째, 전부 Docker화되어
> 있고 크리덴셜 없이 전체 라이프사이클을 돌리는 데모 모드가 있어서, 이
> 워크플로우를 CI에서 매 PR마다 시뮬레이션합니다.

### 3:30 — 관측성 (How에 포함) · ~30초

*탭 5 — Leadership 탭.*

> 이 화면이 **Blue Team SOC** — 방어자의 관제 콘솔입니다. "내가 엔지니어링 리더라면 이게 돌아가는지 어떻게 알까?" 이 탭이 한 문장으로
> 답합니다. 판정 — healthy, in progress, attention, budget blocked — 와
> 함께 탐지→PR 커버리지, PR까지 중간값 시간, 수정당 평균 ACU, 예산 소진율.
> 모든 서비스가 한 줄 JSON 이벤트도 내보내기 때문에 기존 SIEM에 바로
> 붙습니다.

### 4:00 — Why Devin · ~35초

> 왜 스크립트나 자동 수정 기능이 있는 스캐너가 아니라 자율 에이전트인가?
> 이 수정들은 템플릿화가 안 되기 때문입니다. 해싱 방식 교체, 수집 경로에서
> pickle 로드 제거, 고정된 의존성을 올리고 깨지는 부분 수정 — 각각 주변
> 코드를 읽고, 판단하고, 근거가 담긴 리뷰 가능한 PR을 만들어야 합니다. 룰
> 엔진은 못 하고, 사람은 새벽 3시에 모든 finding에 대해 할 수 없습니다.
> Devin은 정확히 그 틈에 들어가고, API 덕분에 가드레일 뒤에 놓고 파이프라인의
> 다른 서비스처럼 다룰 수 있습니다.

### 4:35 — When (다음 단계) · ~25초

> 지금은 한 영역 — 모니터링 스택의 취약점 — 만 다룹니다. 실제 고객
> 프로젝트라면 세 방향으로 확장하겠습니다. 진짜 퍼플팀으로 루프 닫기 — 지금은
> 레드와 블루가 타겟과 텔레메트리를 공유하지만 되돌아가는 것이 없습니다. 수정
> 결과가 레드팀 시나리오와 탐지 규칙에 피드백되어, 한 번 고칠 때마다 다음
> 공격이 더 어려워지게. 적용 범위 확대: 같은 트리거-가드레일-수정 패턴을
> 네트워크 장비 보안 기준선 점검, 레거시 고객 데이터 시스템, 기존 시스템과의
> 호환성·취약점 관리 등 통신사의 개인정보 보호 전 영역에. 그리고 프로덕션
> 강화: 실제 스캐너 연동, consumption API 기반 사용량 텔레메트리, 저위험
> 클래스에 대한 정책 기반 자동 머지.

---

## Checklist before recording

- [ ] Live stack up (`DEMO_MODE=false`), tunnel not required for polling path
- [ ] One injection done ≥ 5 min earlier so a row is already at `pr_open`
- [ ] `__probe__` issue deleted from the fork; open fix PRs reviewed/merged
- [ ] Browser cache cleared; dashboard shows opaque header + local time zone
- [ ] Devin session tab pre-loaded (sessions take a few seconds to render)
