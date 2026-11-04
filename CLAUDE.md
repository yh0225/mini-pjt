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
uv run pytest                                         # 단위 테스트
uv run ruff check . && uv run ruff format .
uv run python -m travel_agent.main                    # (예정, M3) CLI 실행
```

## 아키텍처
Supervisor 패턴 멀티 에이전트. HITL은 `interrupt()`로 멈추고 `Command(resume=...)`로 재개한다.

```
START → intake ──(필수 정보 부족 / 미지원 도시)→ [HITL①] ↺
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

### 노드
- **intake**: 입력에서 `TripRequest` 추출 (structured output, `LIGHT_MODEL`). 필수 필드 누락·미지원 도시 시 HITL①
- **Supervisor**: structured output으로 `next` 결정. 매 결정마다 `step_count += 1`
- **Researcher**: `create_react_agent` 기반. `Candidate` 리스트 반환, `summary_ko`는 항상 한국어
- **Family / Healing Planner**: 승인된 `Candidate`만 사용해 `Itinerary` 생성. `revise_days`가 있으면 해당 날만 재생성
- **Critic**: `rules.py` 검사 결과 + LLM 검토를 `critique`에 기록
- **save**: rich 표 출력, `itinerary.json` / `itinerary.md` 저장 (출처 목록 + CC BY-SA 문구)

### Researcher 도구
| 도구 | 동작 |
|---|---|
| `search_places(query, city, theme)` | Chroma 검색. `city`로만 필터하고, `theme`은 태그가 있으면 순위를 올리는 신호로만 쓴다 (테마 필터를 걸면 Hit@5 90% → 85%) |
| `search_blog(query)` | 네이버 블로그 검색 API. 결과는 저장하지 않음 |
| `estimate_travel_time(place_a, place_b, transport)` | haversine 거리 × 이동 수단별 보정계수. 좌표 없으면 30분 |

### 구현 상수
| 항목 | 값 |
|---|---|
| Critic 재작업 상한 (`retry_count`) | 2 → 초과 시 `warnings`에 기록하고 HITL③ |
| Supervisor 결정 상한 (`step_count`) | 10 → 초과 시 현재 결과로 HITL③ |
| LangGraph `recursion_limit` | 50 |
| LLM 호출 재시도 / 파싱 실패 재요청 | 지수 백오프 최대 3회 / 최대 2회 |
| 후보 장소 개수 | 약 10개, 카테고리(관광·식사·휴식) 균형 |
| 낮잠 규칙 적용 | `min(children_ages) <= 6` |

## 데이터 모델 (`state.py`)
```python
class TripRequest(BaseModel):
    destination, nights, days, start_date, companions, budget  # 모두 Optional
    children_ages: list[int] = []        # family 테마 필수
    theme: Literal["family", "healing"] | None
    preferences: list[str] = []
    transport: str | None                # 없으면 cities.yaml 도시 기본값

class Candidate(BaseModel):
    place, city, category, summary_ko, source_url: str
    lat, lon: float | None
    approved: bool = True

class Activity(BaseModel):
    start, end: str                      # "HH:MM"
    title, place: str
    category: Literal["sight", "meal", "rest", "move", "activity"]
    note, source_url: str | None

class DayPlan(BaseModel):     day: int; summary: str; activities: list[Activity]
class Itinerary(BaseModel):   title, theme; days: list[DayPlan]; tips, warnings: list[str]

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    request: TripRequest
    candidates: list[Candidate]
    itinerary: Itinerary | None
    critique: list[str]
    retry_count, step_count: int
    revise_days: list[int]
    next: str
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
- 네이버 블로그 결과는 **적재하지 않는다** (실시간 도구 전용)

## 환경 변수 (`.env` → `config.py`)
```
LLM_PROVIDER=            # 선택: anthropic | bedrock (비우면 ANTHROPIC_API_KEY → AWS 키 순으로 자동)
ANTHROPIC_API_KEY=
AWS_ACCESS_KEY_ID= / AWS_SECRET_ACCESS_KEY= / AWS_DEFAULT_REGION=
BEDROCK_PROFILE_PREFIX=  # 선택: 1st-party ID에 붙일 접두어 (기본 us)
NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=
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
├── main.py                  # (M3) CLI 루프 (interrupt 처리, thread_id)
├── graph.py                 # (M3) StateGraph 조립
├── state.py                 # (M2~) State, Pydantic 모델
├── config.py                # 경로·모델·환경 변수
├── llm.py                   # Chat 모델 생성 (Anthropic / Bedrock)
├── agents/                  # (M2~) supervisor, researcher, planners, critic
├── tools/                   # (M2~) search_places, search_blog, estimate_travel_time
├── prompts/                 # 프롬프트 파일 (tagger.md, ...)
├── rules.py                 # (M5) 테마 규칙 검사 (순수 함수)
└── ingest/                  # __main__, cities, crawler, chunk, tagger, index, search
tests/
├── test_chunk.py            # 위키텍스트 파서 단위 테스트
├── test_rules.py            # (M5) 테마 규칙 단위 테스트
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
- 새 테마는 `agents/` Planner + `prompts/` 프롬프트 + `rules.py` 규칙 추가로 확장한다
- `rules.py`는 LLM을 호출하지 않는 **순수 함수**로, 규칙마다 pytest 단위 테스트를 둔다. 기준값은 SERVICE.md 8절

### 데이터·보안
- `.env`, `data/raw/`, `data/chroma/`, `data/checkpoints.db`는 커밋하지 않는다
- 키는 `.env.example`에 이름만 둔다
- 네이버 API 결과는 디스크에 저장하지 않는다

### Git
- 커밋 메시지는 Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`)

### 문서
- `SERVICE.md`와 `CLAUDE.md`는 사용자가 **"문서 반영"을 요청할 때만** 수정한다
- 제품 결정(무엇을·왜)은 SERVICE.md, 구현 결정(어떻게)은 CLAUDE.md에 둔다

## 주의 / 미결정
- Python 3.14에서 torch 2.15·chromadb 1.5·sentence-transformers 6.2 동작 확인됨
- bge-m3는 약 2GB, 최초 1회 다운로드 (이후 HF 캐시 사용)
- Windows 터미널에서 한국어 출력 시 `PYTHONIOENCODING=utf-8` 필요할 수 있음
- 미결정: `estimate_travel_time` 보정계수
