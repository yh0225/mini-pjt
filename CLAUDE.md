# CLAUDE.md

## 기술 스택
| 영역 | 선택 |
|---|---|
| 언어 / 패키지 | Python 3.14 (`.venv`) / `uv` + `pyproject.toml` |
| 에이전트 | LangChain + LangGraph |
| LLM | `langchain-anthropic` — 에이전트: `claude-sonnet-5-5`, 태깅·intake: `claude-haiku-5-5` |
| RAG | 로컬 HuggingFace `BAAI/bge-m3` + Chroma (`data/chroma/`) |
| 체크포인터 | LangGraph `SqliteSaver` (`data/checkpoints.db`) |
| 기타 | Pydantic v2, `rich`(CLI 출력), `ruff`, `pytest` |

## 명령어 (예정)
```bash
uv sync                                   # 의존성 설치
uv run python -m travel_agent.ingest      # 위키보이지 크롤링 → Chroma 적재
uv run python -m travel_agent.main        # CLI 실행 (--thread <id>로 이어서 진행)
uv run pytest                             # 테스트
uv run ruff check . && uv run ruff format .
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
| `search_places(query, city, theme)` | Chroma 검색 (`city`, `themes` 메타데이터 필터) |
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
data/cities.yaml → crawler (위키보이지 ko/en) → data/raw/ → 정제 → 청크 분할
→ tagger (LIGHT_MODEL) → bge-m3 임베딩 → Chroma
```
- `cities.yaml`: 도시마다 `name`, `wikivoyage_ko`, `wikivoyage_en`, `transport` (`car`/`taxi`/`transit`)
- 청크 메타데이터: `city`, `place`, `themes`, `lang`, `source`, `url`, `lat`, `lon`
- 좌표는 위키보이지 listing 템플릿의 `lat`/`long`에서 추출, 없으면 `None`
- 크롤러: `robots.txt` 확인, 요청 간 지연, 명시적 User-Agent, `data/raw/`에 캐시해 재요청하지 않음
- 페이지 단위 실패는 건너뛰고 로그로 남긴 뒤 계속 진행
- 네이버 블로그 결과는 **적재하지 않는다** (실시간 도구 전용)

## 환경 변수 (`.env` → `config.py`)
```
ANTHROPIC_API_KEY=
NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=
AGENT_MODEL=claude-sonnet-5-5
LIGHT_MODEL=claude-haiku-5-5
```

## 폴더 구조
```
data/
├── cities.yaml
├── raw/                     # 크롤링 원문 캐시
├── chroma/                  # 벡터 DB
└── checkpoints.db
src/travel_agent/
├── main.py                  # CLI 루프 (interrupt 처리, thread_id)
├── graph.py                 # StateGraph 조립
├── state.py                 # State, Pydantic 모델
├── config.py                # 모델·경로·환경 변수
├── agents/                  # supervisor, researcher, planners, critic
├── tools/                   # search_places, search_blog, estimate_travel_time
├── prompts/                 # 에이전트·테마별 프롬프트 파일
├── rules.py                 # 테마 규칙 검사 (순수 함수)
└── ingest/                  # crawler, chunk, tagger, index
tests/
├── test_rules.py            # 테마 규칙 단위 테스트 (LLM 호출 없음)
└── eval_retrieval.py        # 질문↔정답 장소 약 20쌍, Chroma Hit@5
```

## 규약
### 코드
- 코드·식별자·주석은 **영어**, 사용자에게 보이는 출력은 **한국어**
- 모든 함수에 타입 힌트, 데이터 구조는 Pydantic v2 모델
- `ruff check`와 `ruff format`을 통과해야 한다
- 모델 ID·경로·키는 하드코딩하지 않고 `config.py`를 통해 읽는다
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
- Python 3.14에서 torch·chromadb 설치 실패 시 3.12 가상환경으로 전환
- bge-m3는 약 2GB, 최초 1회 다운로드 후 배치 적재
- 미결정: 청크 크기·overlap, `estimate_travel_time` 보정계수
