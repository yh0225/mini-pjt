# CLAUDE.md

## 기술 스택
| 영역 | 선택 |
|---|---|
| 언어 / 패키지 | Python 3.14 (`.venv`) / `uv` + `pyproject.toml` |
| 에이전트 | LangChain + LangGraph |
| LLM | Claude — Anthropic API(`langchain-anthropic`) 또는 Amazon Bedrock(`langchain-aws`), `llm.py`가 선택 |
| 모델 (현재) | Bedrock us-west-2: 에이전트 `us.anthropic.claude-sonnet-4-6`, 태깅·intake `us.anthropic.claude-haiku-4-5-20251001-v1:0` (5.5 계열은 계정 권한 없음, 권한이 생기면 `.env`만 변경) |
| RAG | 로컬 HuggingFace `BAAI/bge-m3` + Chroma (`data/chroma/`) |
| 체크포인터 | LangGraph `SqliteSaver` (`data/checkpoints.db`) |
| 기타 | Pydantic v2, `rich`(CLI 출력), `ruff`, `pytest` |

## 명령어
```bash
uv sync                                               # 의존성 설치
uv run python -m travel_agent.ingest                  # 적재 전체 (crawl → chunk → tag → index)
uv run python -m travel_agent.ingest --city jeju --steps crawl chunk   # 도시·단계 선택
uv run python -m travel_agent.ingest.search "아이랑 갈 수족관" --city okinawa  # 검색 확인
uv run python tests/eval_retrieval.py [--theme]       # 검색 품질 Hit@5
uv run python -m travel_agent.agents.researcher --city jeju --theme family --days 4 --children 5  # Researcher 단독 실행
uv run pytest                                         # 단위 테스트
uv run ruff check . && uv run ruff format .
uv run python -m travel_agent.main [--thread <id>]    # CLI 실행 / 대화 이어서 하기
```

## 아키텍처
Supervisor 패턴 멀티 에이전트. HITL은 `interrupt()`로 멈추고 `Command(resume=...)`로 재개한다.

```
START → intake ──(필수 정보 부족)→ [HITL①] ↺   ──(미지원 도시)→ reject → END
          ↓
      Supervisor ◀────────────────────────────────────┐
       ├→ Researcher (ReAct + 도구)                    │
       │     ↓ [HITL②] 후보 장소 승인 ─────────────────┤
       ├→ Family Planner  ─────────────────────────────┤
       ├→ Healing Planner ─────────────────────────────┤
       ├→ Critic (rules.py + LLM 검토) ─────────────────┤
       └→ [HITL③] 최종 승인
                ├─ 수정 요청 → Supervisor (revise_days만 재생성)
                └─ 승인 → save → END
```
구현 완료(M5). 모든 작업자는 끝나면 supervisor로 돌아간다. 허용 순서(`allowed_workers`): research → (approve) → planner → critic → (`revise_days` 있으면 planner) → final_approval → save → finish. `approve`는 추가 요청이면 research, 애매하면 다시 approve

### 노드
- **intake**: LLM(`LIGHT_MODEL`)은 `IntakeExtraction` 추출만 한다. 병합(`merge_request`), 박/일 보정, 필수 필드 판단(`missing_fields`), 질문 문구(`build_question`)는 결정적 코드. 대화 전체를 매번 다시 읽어 최신 조건을 만든다
- **ask_user** (HITL①): `interrupt({"type": "ask", "question"})` → 답을 메시지에 추가하고 intake로
- **reject**: 미지원 도시면 (부족 정보가 있어도 먼저) `unsupported_message()`로 지원 도시 목록을 안내하고 END. CLI는 종료된 대화를 다시 열어도 같은 안내만 보여준다
- **approve** (HITL②): `interrupt({"type": "approve_candidates", "candidates", "notice"})` → `approval.parse_reply()`가 `remove`/`add_requests`/`approve`로 해석. 뺀 후보는 `approved=False`로 남긴다
- **Supervisor** (`LIGHT_MODEL`): structured output으로 `next`·`brief`(작업자에게 줄 영어 지시)·`reason`(사용자에게 보일 한국어 한 줄) 결정. `allowed_workers(state)`가 상태별 허용 작업자를 정하고 `enforce()`가 허용 밖 선택을 기본값으로 덮는다. 매 결정마다 `step_count += 1`. 20회 이후엔 재작업·검증을 건너뛰고 final_approval → save로 마무리
- **Researcher**: `langchain.agents.create_agent` (ReAct) + `response_format=ToolStrategy(CandidateList)`. `summary_ko`·`reason_ko`는 항상 한국어. 결과는 `ground_candidates()`로 인덱스에서 재조회해 `source_url`·좌표를 채우고 인덱스에 없는 장소는 버린다
- **Family / Healing Planner**: 구현 하나(`agents/planner.py`)를 공유하고 프롬프트(`planner_base.md` + `planner_{theme}.md`)만 다르다. `create_agent` + `estimate_travel_time` + `ToolStrategy(Itinerary)`. 승인된 `Candidate`만 사용. `ground_itinerary()`가 `source_url`을 채운다. `revise_days`가 있으면 현재 일정 + 고칠 이유를 주고 다시 만든 뒤 `merge_days()`가 **그 날짜만** 반영한다 (여행 팁은 새 결과로 교체, 제목은 유지)
- **Critic**: `rules.check_itinerary()`(권위) + `llm_review()`(미등록 장소·중복 방문·모순, 실패해도 무시). 위반이 있고 `retry_count < 2`면 위반 날짜를 `revise_days`로(사유에 `traveller_request` 원문 포함), 아니면 남은 위반을 `itinerary.warnings`로 덮어쓰고 `itinerary_checked=True`
  - 수정 요청 확인: **코드**(`activities_to_judge`)가 요청 날짜 + 시간대(`REQUEST_WINDOWS`: 오전·아침 <12, 점심 11–14, 오후 12–18, 저녁·밤 ≥18, 없으면 하루 전체)의 활동만 고르고(이동·식사 제외), LLM은 날짜별 `request_checks`(`satisfied`)만 답한다. `satisfied=False`만 `rule="request"` 위반
- **final_approval** (HITL③): `interrupt({"type": "approve_itinerary", "itinerary"})` → `parse_final_reply()` → 승인(`traveller_request` 비움) / 고칠 날짜(`revise_days`·`traveller_request(_days)` 저장, `retry_count` 0) / "전체 다시"는 모든 날짜
- **save**: `output.save_itinerary()` → `output/<thread_id>/itinerary.{json,md}` (사용한 장소의 출처 + CC BY-SA 문구)

### Researcher 도구
| 도구 | 동작 |
|---|---|
| `search_places(query, city, theme)` | Chroma 검색. `city`로만 필터하고, `theme`은 태그가 있으면 순위를 올리는 신호로만 쓴다 (테마 필터를 걸면 Hit@5 90% → 85%) |
| `estimate_travel_time(city, place_a, place_b, transport)` | 인덱스에서 두 장소 좌표 조회 → haversine × 보정계수(아래). 1km 이하는 도보, 좌표 없으면 30분 |

### 구현 상수
| 항목 | 값 |
|---|---|
| Critic 재작업 상한 (`retry_count`) | 2 → 초과 시 `warnings`에 기록하고 HITL③ |
| Supervisor 결정 상한 (`step_count`) | 20 (기본 흐름만 약 10단계) → 초과 시 현재 결과로 HITL③ |
| 규칙 판정 기준 | 온종일 = 16:00 이후까지, 낮잠 = 12:00~15:00 시작·60분+, 점심 = 11:30~13:00 시작 (`rules.py` 상수) |
| LangGraph `recursion_limit` | 50 |
| LLM 호출 재시도 / 파싱 실패 재요청 | 지수 백오프 최대 3회 / 최대 2회 |
| 후보 장소 개수 | 약 10개, 카테고리(관광·식사·휴식) 균형 |
| 낮잠 규칙 적용 | `min(children_ages) <= 6` |
| 테마 순위 보정 (`THEME_BOOST`) | 태그된 장소의 cosine 거리 −0.05, 3배수로 가져와 재정렬 |
| 이동 시간 (우회 계수 / 속도 / 추가 시간) | car 1.3·40km/h·+5분, taxi 1.3·25km/h·+5분, transit 1.4·22km/h·+10분, 도보 1.2·4.5km/h (초기값) |
| Researcher `recursion_limit` | 기본 30, 추가 요청 16. 초과 시 `GraphRecursionError`를 잡아 빈 결과 + 안내 문구 |
| Planner `recursion_limit` | 40 (이동 시간 확인 5~8회 권장) |

## 데이터 모델 (`state.py`)
```python
class TripRequest(BaseModel):
    destination, nights, days, start_date, companions, budget  # 모두 Optional
    children_ages: list[int] = []        # family 테마 필수
    theme: Literal["family", "healing"] | None
    preferences: list[str] = []
    transport: str | None                # 없으면 cities.yaml 도시 기본값

class Candidate(BaseModel):
    place, city, summary_ko, reason_ko, source_url: str   # place = 인덱스의 정확한 이름
    category: Literal["sight", "activity", "meal", "rest", "shop"]
    lat, lon: float | None
    approved: bool = True

class Activity(BaseModel):
    start, end: str                      # "HH:MM"
    title, place: str
    category: Literal["sight", "meal", "rest", "move", "activity"]
    note, source_url: str | None

class DayPlan(BaseModel):     day: int; summary: str; activities: list[Activity]
class Itinerary(BaseModel):   title, theme; days: list[DayPlan]; tips, warnings: list[str]
# warnings는 시스템 점검 전용. LLM이 쓴 warnings는 ground_itinerary()가 tips로 옮긴다

class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    request: TripRequest
    missing: list[MissingField]          # destination / duration / theme / children_ages
    unsupported_destination: str | None   # 있으면 reject → END
    candidates: list[Candidate]
    add_requests: list[str]; notice: str | None; candidates_confirmed: bool   # HITL②
    next: Worker; brief: str; step_count: int; itinerary: Itinerary | None
    revise_days: list[int]; revise_reason: str; retry_count: int           # 재작업
    itinerary_checked, final_approved: bool; critique: list[str]; saved_paths: list[str]
    traveller_request: str; traveller_request_days: list[int]   # HITL③ 수정 요청 (Critic이 확인)
```
(위는 필드 요약이다. 실제 코드에서는 필드마다 타입을 명시한다.)

## RAG 적재 파이프라인 (`ingest/`)
```
cities.yaml → crawler → data/raw/<lang>/*.wiki → chunk → data/raw/chunks/<city>.jsonl
→ tagger (LIGHT_MODEL) → data/raw/tags/<city>.json → index (bge-m3) → Chroma "places"
```
- `cities.yaml`: 도시마다 `id`, `name_ko`, `transport` (`car`/`taxi`/`transit`), `wikivoyage_en`/`wikivoyage_ko` (문서 제목 목록, 큰 도시는 구 단위 문서 포함)
- 수집: `/wiki/<title>?action=raw` (en은 `/w/api.php`가 robots.txt로 막힘). 넘겨주기 문서는 따라가고(ko의 `Wy/ko/` 접두어 제거), `robots.txt` 확인, 요청 간 1초, 명시적 User-Agent, 캐시가 있으면 재요청하지 않음 (`--refresh`로 강제)
- 청크: listing 템플릿(`see`/`do`/`eat`/`drink`/`buy`, `sleep` 제외)은 장소 하나당 청크 하나, 설명 글은 800자·overlap 100. 장소는 도시 안에서 이름 기준으로 중복 제거 (좌표 있는 쪽 우선)
- 메타데이터: `city`, `lang`, `source`, `url`, `page`, `section`, `kind`(`listing`/`prose`), `place`, `category`, `lat`, `lon`, `themes`(문자열) + `theme_family`/`theme_healing`(bool — Chroma는 리스트 메타데이터를 못 씀)
- 태깅: 25개씩 묶어 structured output, 도시별 캐시로 새 청크만 태깅. 파싱 실패만 재시도하고 권한·접근 오류는 즉시 실패
- 적재: 도시 단위로 기존 문서를 지우고 다시 넣는다 (재실행해도 결과 동일)
- 단계별 실패는 건너뛰고 `data/logs/ingest.log`에 남긴다

## 환경 변수 (`.env` → `config.py`)
```
LLM_PROVIDER=            # 선택: anthropic | bedrock (비우면 ANTHROPIC_API_KEY → AWS 키 순으로 자동)
ANTHROPIC_API_KEY=
AWS_ACCESS_KEY_ID= / AWS_SECRET_ACCESS_KEY= / AWS_DEFAULT_REGION=
BEDROCK_PROFILE_PREFIX=  # 선택: 1st-party ID에 붙일 접두어 (기본 us)
AGENT_MODEL=             # 1st-party ID(claude-sonnet-5-5) 또는 Bedrock ID 그대로
LIGHT_MODEL=
```

## 폴더 구조
```
data/
├── cities.yaml
├── raw/                     # 원문(en/, ko/), chunks/, tags/ 캐시
├── chroma/                  # 벡터 DB
├── logs/
└── checkpoints.db
src/travel_agent/
├── main.py                  # CLI 루프 (interrupt 처리, thread_id, 중단 지점 재개)
├── graph.py                 # StateGraph 조립, make_checkpointer()
├── state.py                 # TripRequest, Candidate, Activity/DayPlan/Itinerary, AgentState
├── config.py                # 경로·모델·환경 변수
├── llm.py                   # Chat 모델 생성 (Anthropic / Bedrock)
├── ui.py / output.py        # rich 표·요약 문구 / JSON·Markdown 저장
├── agents/                  # intake, approval, researcher, supervisor, planner, critic, final_approval
├── tools/                   # places.py (search_places), travel.py (estimate_travel_time)
├── prompts/                 # 에이전트별 프롬프트 (.md)
├── rules.py                 # 테마 규칙 검사 (순수 함수)
└── ingest/                  # __main__, cities, crawler, chunk, tagger, index, search
tests/
├── test_chunk.py            # 위키텍스트 파서 단위 테스트
├── test_tools.py            # 순위 보정·이동 시간 단위 테스트
├── test_intake.py           # intake 병합·필수 필드·질문, 후보 제거 단위 테스트
├── test_supervisor.py       # 라우팅 허용 순서, 일정 grounding
├── test_rules.py / test_critic*.py / test_output.py   # 규칙, Critic 판단·날짜 병합, 파일 출력
└── eval_retrieval.py        # 한국어 질문↔정답 장소 20쌍, Hit@5 (pytest 대상 아님)
```

## 규약
### 코드
- 코드·식별자·주석은 **영어**, 사용자에게 보이는 출력은 **한국어**
- 모든 함수에 타입 힌트, 데이터 구조는 Pydantic v2 모델
- `ruff check`와 `ruff format`을 통과해야 한다
- 모델 ID·경로·키는 하드코딩하지 않고 `config.py`를 통해 읽는다
- Chat 모델은 직접 만들지 않고 `llm.get_chat_model("agent" | "light")`로만 만든다
- `temperature` 등 샘플링 파라미터는 넣지 않는다 (최신 Claude 모델은 기본값이 아니면 400)
- 프롬프트는 인라인으로 쓰지 않고 `prompts/` 파일로 분리한다
- 새 테마는 `prompts/planner_{theme}.md` + 그래프 노드(`make_planner_node`) + `rules.py` 규칙 추가로 확장한다
- `rules.py`는 LLM을 호출하지 않는 **순수 함수**로, 규칙마다 pytest 단위 테스트를 둔다. 기준값은 SERVICE.md 8절
- LLM은 추출·생성만 맡기고, 판단(필수 필드, 라우팅, 후보 제거)은 결정적 코드로 한다
- 상태에 새 Pydantic 모델을 넣으면 `graph.CHECKPOINT_TYPES`에 등록한다 (미등록 타입은 체크포인트 복원 시 차단 예정)
- interrupt 노드에서 `interrupt()` 앞에는 부작용 있는 코드를 두지 않는다 (재개 시 노드가 처음부터 다시 실행됨)
- Chroma 접근은 `tools.places._store()`를 거친다 (병렬 도구 호출 대비 잠금으로 한 번만 연다)

### 데이터·보안·Git
- `.env`, `data/raw/`, `data/chroma/`, `data/checkpoints.db`, `output/`은 커밋하지 않는다
- 키는 `.env.example`에 이름만 둔다
- 커밋 메시지는 Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`)

### 문서
- `SERVICE.md`와 `CLAUDE.md`는 사용자가 **"문서 반영"을 요청할 때만** 수정한다
- 제품 결정(무엇을·왜)은 SERVICE.md, 구현 결정(어떻게)은 CLAUDE.md에 둔다

## 주의 / 미결정
- bge-m3는 약 2GB, 최초 1회 다운로드. Windows에서 한국어 출력 시 `PYTHONIOENCODING=utf-8` 필요할 수 있음
- Researcher 후보는 프롬프트로 10곳, 추가 요청은 "요청 수만큼, 최대 3곳"
- 힐링 "주요 활동 사이 30분" 규칙은 같은 단지 안 연속 관람에도 적용된다 (현행 유지 결정, SERVICE.md 16절)
