# onion_backend_v1.2

이슈 엔진 MVP Lite — **비용 ₩0 스택**으로 "이슈 단위로 재구성된 뉴스" 가설을 검증하는 파이프라인.

GitHub Actions(수집·처리) + SQLite(상태) + GitHub Pages(서빙). DB 서버·유료 API 없음.

## 아키텍처

```
L1 수집 (Actions cron)             L2 처리 (Actions cron 1시간)         L3 서빙 (Pages)
─────────────────────             ──────────────────────────         ─────────────
DART·EDGAR·GDELT·부처RSS (30분)    raw 로드 → simhash 중복제거          out/index.json
FRED·ECOS·[시세] (1시간)      →    → e5-small 임베딩(384d)        →    out/issues/{id}.json
→ data/raw/YYYY-MM/*.jsonl        → 증분 군집(τ_join 0.82)             front/index.html
→ data/express/*.json (급행)      → 급행 병합 → 분류 → 생애주기          (Chart.js)
                                  → Gemini→Groq→템플릿 가공
                                  → export → engine.db 커밋
```

핵심 원칙 (타협 불가):
1. **저작권 방화벽** — 기사 본문 저장 금지. 제목+리드 200자만. 표현은 항상 자체 생성.
2. **공식 데이터 = 1차 진실** — 수치는 FRED·ECOS·DART·EDGAR 에서 직접 (LLM 추출·환각 0).
3. **발행 이슈 동결** — 발행 후 멤버 추가만 가능. 재군집 셔플 없음.

## 빠른 시작 (로컬)

```bash
pip install -r requirements.txt
cp .env.example .env          # 키 채우기 (아래 §키 발급)
python -m pytest              # 오프라인 테스트 (네트워크·키 불필요) — 전부 pass 확인
python -m engine.run_pipeline --dry-run   # 합성 데이터 e2e (모델·키 불필요)
python -m engine.run_collect --group all  # 실수집 1회
python -m engine.run_pipeline             # 실처리 1회 (최초 실행 시 e5-small ~470MB 다운로드)
```

## 셋업 체크리스트 (사용자 직접 수행)

### 1. 키 발급 (전부 무료·카드 불요)
| 키 | 어디서 | 소요 |
|----|--------|------|
| `DART_API_KEY` | opendart.fss.or.kr | 즉시 |
| `ECOS_API_KEY` | ecos.bok.or.kr/api | ~1일 |
| `FRED_API_KEY` | fred.stlouisfed.org/docs/api/api_key.html | 즉시 |
| `GEMINI_API_KEY` | aistudio.google.com (⚠️ 무료 티어 입력은 구글 학습에 활용될 수 있음) | 즉시 |
| `GROQ_API_KEY` | console.groq.com | 즉시 |
| `DATA_GO_KR_API_KEY` (선택) | data.go.kr '주식시세정보' 활용신청 | 수시간 |

### 2. GitHub 설정
1. **Settings → Secrets and variables → Actions** 에 위 키들 등록.
2. **Settings → Pages** → Source: `Deploy from a branch` → Branch: `gh-pages` / `/ (root)`.
   (gh-pages 브랜치는 첫 pipeline 실행이 자동 생성)
3. Actions 탭에서 `collect-fast` → **Run workflow** 로 첫 수집 확인, 이어서 `pipeline` 수동 실행.
4. 확인: `https://<계정>.github.io/onion_backend_v1.2/` 에서 이슈 카드가 뜨면 성공.

### 3. 운영 확인 항목 (주 1회 권장)
- **부처 RSS URL**: `config.yaml` `collect.gov_rss.feeds` 의 URL 5개는 초안 — 사이트 개편으로
  바뀔 수 있으니 실제 접속해 확인·교정 (실패해도 다른 소스는 계속 돈다).
- **라이선스**: 사용하는 FRED 시리즈의 Copyright 표기 유무(있으면 제거),
  ECOS 통계의 "한국은행 작성" 여부 확인.
- **검수 큐**: LLM 검증 실패 이슈 확인 —
  `sqlite3 engine.db "select * from review_queue"`
- **무료 한도**: AI Studio 대시보드에서 Gemini 실사용량 확인 (한도 수시 변동).
- **황금세트 20건**: 실제 이슈 20개에 대해 ①묶여야 할 기사쌍 표시(τ 튜닝)
  ②이상적 one_liner/details/effects 수기 작성 → `engine/llm/prompt.py` few-shot 교체.

## 구조

| 경로 | 역할 |
|------|------|
| `config.yaml` | 하이퍼파라미터·소스 설정 전부 (코드 하드코딩 금지) |
| `entities.yaml` / `categories.yaml` | 개체 사전 / 8 카테고리 키워드·프로토타입 |
| `engine/collectors/` | L1 수집기 6종 + 시세(선택). 급행 트리거 → `data/express/` |
| `engine/express.py` | 급행 이벤트 → 즉시 발행 이슈 (군집 스킵, anchor_key 멱등) |
| `engine/normalize.py` `dedup.py` `embed.py` `cluster.py` | §04 사이클: 정규화→중복제거→임베딩→증분군집 |
| `engine/classify.py` `lifecycle.py` | 3단 분류 폴백 / stale·archive |
| `engine/llm/` | Gemini→Groq→템플릿 체인, 검증(어미·숫자대조·금지어), fact_hash 캐시 |
| `engine/viz.py` | visual_type 화이트리스트 + 공식 API 시리즈 (6h 캐시) |
| `engine/export.py` | 정적 JSON export |
| `front/index.html` | 카드 피드 + 상세 + Chart.js (빌드 도구 없음) |
| `.github/workflows/` | collect-fast(30분) · collect-slow(1시간) · pipeline(1시간) |

## 운영 메모

- **상태**: 전부 `engine.db`(SQLite) 한 파일. 백업 = 파일 복사. L2 워크플로가 커밋으로 보존.
- **관측성**: `sqlite3 engine.db "select value from meta where key='run_history'"` — 최근 실행 20건.
- **push 경합**: 워크플로 3개는 `concurrency: repo-commit` 그룹으로 직렬화 + rebase 재시도.
- **용량**: archived 이슈의 임베딩은 자동 제거(prune), raw 는 월별 파일 로테이션.
- **60일 규칙**: L1 이 상시 커밋하므로 Actions 자동 비활성화(60일 무커밋) 자연 회피.
- **튜닝**: `config.yaml` — `tau_join`(낮추면 더 잘 묶임) · `merge_sim` · `llm.daily_cap` 등.

## 출처 표기

GDELT (gdeltproject.org) · FRED (미 정부 시리즈) · 한국은행 ECOS · DART · SEC EDGAR.
기사 본문 전재 금지 — 제목/요약/링크만. 부처 보도자료(공공저작물)만 원문 인용 가능.
프런트 하단·이슈 상세에 고정 면책 문구 렌더 ("투자 판단의 근거가 아닙니다").
