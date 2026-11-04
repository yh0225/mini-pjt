# 테마별 여행 일정 Agent

여행 **목적(테마)**에 맞춰 일정을 짜 주는 터미널 챗봇입니다.
아이와 함께하는 **가족 여행**과 쉬러 가는 **힐링 여행**을 지원하며, 여행 가이드 자료(위키보이지)를 근거로 장소를 고르고, 테마 규칙을 스스로 검증한 뒤, 사용자의 승인을 받아 일정을 완성합니다.

LangChain · LangGraph · RAG · ReAct · Human-in-the-loop · Multi-agent를 하나의 흐름으로 연결해 보는 학습용 프로젝트입니다.

## 주요 기능

- **대화로 조건 파악** — "제주도 3박4일 아이랑" 같은 말에서 조건을 뽑고, 부족한 정보(아이 나이 등)만 되묻습니다
- **근거 기반 장소 추천** — 국내외 18개 도시의 위키보이지 자료에서 후보 장소를 찾고, 자료에 없는 장소는 추천하지 않습니다
- **테마별 일정** — 가족 여행은 짧은 동선·낮잠 시간·이른 귀가, 힐링 여행은 적은 활동·충분한 여유 시간
- **자동 규칙 검증** — 규칙을 어긴 날만 다시 만들고(최대 2회), 남은 문제는 ⚠️로 알려 줍니다
- **사용자 확인과 부분 수정** — 후보 장소와 최종 일정을 승인받고, "2일차 오후는 실내로 바꿔줘" 같은 요청은 해당 날짜만 고칩니다
- **이어서 하기** — 대화가 저장되어, 종료했다가 같은 대화 id로 이어서 진행할 수 있습니다
- **결과 저장** — 일정표를 JSON과 Markdown으로 저장하고, 사용한 장소의 출처를 함께 남깁니다

지원 도시: 서울 · 제주 · 부산 · 강릉 · 경주 · 여수 · 전주 · 오사카 · 도쿄 · 후쿠오카 · 오키나와 · 다낭 · 나트랑 · 방콕 · 발리 · 괌 · 타이베이 · 싱가포르

## 사용 예

```
$ uv run python -m travel_agent.main
새 대화를 시작해요 (id: 3f9a1c2e)
어떤 여행을 계획하고 계신가요?
› 제주 2박3일 4살 아이랑 가족여행
✓ 여행 조건 정리
  제주 · 2박 3일 · 가족 · 아이 4세 · 렌터카
🧑‍✈️ Supervisor → 장소 조사 (Researcher)
  🔎 search_places: 아이와 함께 가기 좋은 관광지
  ...
💬 후보 장소 10곳을 찾았어요.        ← 후보 표 (뺄 곳·추가할 곳 입력)
› 좋아요
🧑‍✈️ Supervisor → 가족 일정 플래너
🧑‍✈️ Supervisor → 규칙 검증 (Critic)
✗ 규칙 위반 4건 → 해당 날짜를 다시 만들어요
  · 1일차: 이동이 4번이에요. 아이와 함께라면 하루 3번 이하로 줄여 주세요.
  ...
✓ 규칙 검증 통과
✈️  제주 가족 여행 ...                ← 날짜별 일정표
› 2일차 오후는 실내 위주로 바꿔줘
  ✏️  2일차만 다시 만들어요
› 좋아요 이대로 저장해줘
✅ 일정을 저장했어요.
  📄 output/3f9a1c2e/itinerary.json
  📄 output/3f9a1c2e/itinerary.md
```

중간에 `종료`(또는 Ctrl+C)를 입력하면 대화가 저장되고, `--thread <id>`로 이어서 진행할 수 있습니다.

## 시작하기

### 1. 준비물

- Python 3.12 이상 (3.14에서 개발·테스트)
- [uv](https://docs.astral.sh/uv/)
- Claude 사용 권한: **Anthropic API 키** 또는 **Amazon Bedrock** 접근 권한(AWS 키)

### 2. 설치

```bash
git clone https://github.com/yh0225/mini-pjt.git
cd mini-pjt
uv sync
```

### 3. 환경 변수

`.env.example`을 `.env`로 복사한 뒤, 둘 중 하나를 채웁니다.

```bash
# A. Anthropic API
ANTHROPIC_API_KEY=...

# B. Amazon Bedrock
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-west-2
```

`AGENT_MODEL`(에이전트용)과 `LIGHT_MODEL`(태깅·조건 추출 등 가벼운 작업용)로 사용할 모델을 지정합니다. `.env.example`의 기본값은 Bedrock 모델 ID이며, Anthropic API를 쓰면 `claude-sonnet-5-5` 같은 ID를 넣습니다.

### 4. 여행 자료 적재 (처음 한 번)

```bash
uv run python -m travel_agent.ingest
```

위키보이지 문서를 내려받아 장소 단위로 나누고, Claude로 테마 태그(가족/힐링)를 붙인 뒤, 로컬 임베딩 모델(`BAAI/bge-m3`, 약 2GB)로 벡터 DB(Chroma)에 저장합니다. 처음 실행 시 모델 다운로드와 18개 도시 처리로 시간이 걸립니다. 원문은 캐시되므로 다시 실행하면 새로 받지 않습니다.

```bash
# 일부 도시·단계만
uv run python -m travel_agent.ingest --city jeju busan --steps crawl chunk
# 검색 확인
uv run python -m travel_agent.ingest.search "아이랑 갈 만한 수족관" --city okinawa
```

### 5. 실행

```bash
uv run python -m travel_agent.main                 # 새 대화
uv run python -m travel_agent.main --thread <id>   # 이어서 하기
```

Windows 터미널에서 한글이 깨지면 `PYTHONIOENCODING=utf-8`을 설정하세요.

## 동작 방식

```
START → intake ──(정보 부족)→ 되묻기 ↺          ──(미지원 도시)→ 안내 후 종료
          ↓
      Supervisor ◀──────────────────────────────┐
       ├→ Researcher (ReAct + RAG)  → 후보 승인 ─┤
       ├→ 가족 / 힐링 Planner (ReAct) ───────────┤
       ├→ Critic (규칙 검사 + AI 검토) ───────────┤
       ├→ 최종 확인 (승인 / 날짜별 수정) ─────────┤
       └→ 저장 → 종료
```

| 구성 요소 | 하는 일 |
|---|---|
| **intake** | 대화에서 여행 조건을 추출하고, 필수 정보가 없으면 되묻습니다 |
| **Supervisor** | 다음에 일할 에이전트와 작업 지시를 정합니다. 허용되지 않은 순서는 코드가 막습니다 |
| **Researcher** | 장소 검색·이동 시간 도구를 반복 호출(ReAct)해 후보 10곳을 고르고, 인덱스에서 다시 확인합니다 |
| **Planner** | 승인된 후보로 날짜별 일정을 만듭니다. 테마별 프롬프트만 다르고 구현은 같습니다 |
| **Critic** | `rules.py`의 테마 규칙(코드)과 짧은 AI 검토로 일정을 점검합니다 |
| **사용자 확인** | LangGraph `interrupt()`로 멈추고 답을 받아 재개합니다 (되묻기 · 후보 승인 · 최종 확인) |

## 기술 스택

| 영역 | 사용 |
|---|---|
| 에이전트 | LangChain, LangGraph (`create_agent`, `interrupt`, `SqliteSaver`) |
| LLM | Claude (Anthropic API 또는 Amazon Bedrock) |
| RAG | 위키보이지 크롤링 → `BAAI/bge-m3` 임베딩 → Chroma |
| 기타 | Pydantic v2, rich, uv, ruff, pytest |

## 프로젝트 구조

```
data/cities.yaml          지원 도시와 위키보이지 문서 목록
src/travel_agent/
  main.py                 CLI
  graph.py                LangGraph 그래프
  agents/                 intake, researcher, supervisor, planner, critic, ...
  tools/                  search_places, estimate_travel_time
  rules.py                테마 규칙 검사 (순수 함수)
  ingest/                 자료 수집·적재 파이프라인
  prompts/                에이전트별 프롬프트
tests/                    단위 테스트, 검색 품질 평가
```

생성되는 데이터(`data/raw/`, `data/chroma/`, `data/checkpoints.db`)와 결과물(`output/`)은 Git에 포함되지 않습니다.

## 테스트

```bash
uv run pytest                              # 단위 테스트 (LLM 호출 없음)
uv run python tests/eval_retrieval.py      # 검색 품질: 한국어 질문 20개 Hit@5
uv run ruff check . && uv run ruff format .
```

## 문서

- [SERVICE.md](SERVICE.md) — 무엇을, 왜 만드는가 (PRD: 사용자, 정책, 테마 규칙, 성공 기준)
- [CLAUDE.md](CLAUDE.md) — 어떻게 만드는가 (아키텍처, 데이터 모델, 구현 규약)

## 데이터 출처와 라이선스

장소 정보는 [위키보이지(Wikivoyage)](https://www.wikivoyage.org/)를 바탕으로 하며 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)을 따릅니다. 수집 시 `robots.txt`를 지키고 요청 사이에 간격을 둡니다. 저장되는 일정 파일에는 사용한 장소의 출처가 함께 기록됩니다. 장소 요약과 번역은 AI가 작성하므로, 방문 전 운영 시간 등을 확인하세요.
