# AI24Times

매일 아침 지난 24시간의 AI 소식을 골라 요약하는 브리핑 사이트입니다.
`data/` 의 JSON을 Jinja 템플릿으로 렌더링해 `dist/` 에 정적 HTML을 만듭니다. 서버가 필요 없습니다.
한국어는 `/`, 영어는 `/en/` 아래에 같은 구조로 만들어지고, 상단 바의 EN/KO 버튼으로 오갑니다.

## 빠르게 시작하기

```bash
pip install -r requirements.txt
python -m generator.build                       # dist/ 생성
python -m http.server 8024 --directory dist     # http://localhost:8024
python -m unittest                              # 테스트
```

Python 3.9 이상, 의존성은 Jinja2 하나입니다.

## 폴더 구조

```
data/
  site.json              사이트 이름·소개·주소·발행 시각·주제·운영자(author)
  ui.json                메뉴·버튼·안내 같은 화면 문구 (ko / en)
  glossary.json          용어사전 (slug → 이름·영문 이름·설명·영문 설명)
  briefings/YYYY-MM-DD.json   하루치 브리핑 한 호
  work/<slug>.json       작업 사례 하나 (파일 이름이 주소가 됨)
  sources.json           기사 수집 출처 (피드 주소·가중치·풀·필터)
  candidates/YYYY-MM-DD.json  수집·선정 결과 (파이프라인이 만듦)
  raw/                   --save-raw 로 저장한 피드 원본 (git 제외)
generator/build.py       빌드 스크립트 (데이터 검증 → 렌더링 → 정적 파일 복사)
pipeline/                기사 수집·선정 파이프라인
  collect.py             실행·기록 (python -m pipeline.collect)
  feeds.py               피드 가져오기·해석 (RSS 2.0 / RSS 1.0 / Atom)
  select.py              거르기 → 점수 → 같은 사건 묶기 → 고르기
  text.py                제목 키워드 (한국어·영어 공통 열쇠), 제목·주소 비교
  verify.py              원문 확인 (본문이 읽히는지)
templates/               Jinja 템플릿
  base.html.j2           상단 바·바닥, 테마 초기화
  _macros.html.j2        페이지 머리, 섹션 머리, 기사 한 건, 작업 스크린샷
  home.html.j2           홈: 최근 브리핑 배너 + 브리핑 목록 + 작업 미리보기 + 사이드바
  daily.html.j2          하루치 브리핑
  archive_index.html.j2  브리핑 목록 + 검색
  work_index.html.j2     작업 사례 목록
  work_detail.html.j2    작업 상세: 개요, 만든 과정 타임라인, 결과 화면, 회고
  topics_index.html.j2, topic.html.j2, glossary.html.j2, feed.xml.j2
static/                  dist/assets/ 로 그대로 복사됨
  css/tokens.css         색·글꼴·크기·간격 토큰 (라이트/다크)
  css/site.css           레이아웃·컴포넌트 (토큰만 사용)
  js/site.js             테마 전환, 배너 넘기기, 용어 패널, 목차 하이라이트, 검색
  work/<slug>/           작업 사례 이미지
tests/test_build.py, tests/test_pipeline.py
```

## 빌드 결과

| 경로 | 내용 |
|---|---|
| `/` | 홈 (최근 브리핑 배너, 브리핑 목록, 작업 미리보기) |
| `/archive/YYYY-MM-DD.html` | 하루치 브리핑 |
| `/archive/` | 월별 목록 + 검색 (`?q=검색어` 로 바로 검색) |
| `/work/`, `/work/<slug>.html` | 작업 사례 목록과 상세 |
| `/topics/`, `/topics/<slug>.html` | 주제별 기사 모음 |
| `/glossary.html` | 용어사전 (`#slug` 로 바로 이동) |
| `/feed.xml` | RSS (최근 20호) |
| `/en/...` | 위 모든 페이지의 영어판 (`/en/feed.xml`, `/en/search-index.json` 포함) |
| `/search-index.json` | 지난 호 검색이 쓰는 색인 |

## 기사 수집·선정 파이프라인

피드에서 지난 24시간 기사를 모아 그날의 후보 10건(+ 국내 소식 3건, 예비 5건)을 고릅니다.
요약·번역은 하지 않습니다. 결과는 `data/candidates/YYYY-MM-DD.json` 에 남습니다.

```bash
python -m pipeline.collect                  # 지금 기준, 원문 확인 포함
python -m pipeline.collect --no-verify      # 원문 확인 없이 (빠름)
python -m pipeline.collect --save-raw       # 받은 피드를 data/raw/날짜/ 에 저장
python -m pipeline.collect --from-raw data/raw/2026-10-29 --now 2026-10-29T08:00+09:00
                                            # 저장해 둔 피드로 그날 실행을 그대로 재현
python -m pipeline.collect --dry-run        # 파일을 쓰지 않고 요약만 출력
```

**단계**

1. **수집**: `data/sources.json` 의 피드를 받습니다. 실패하면 이유(HTTP 코드, 응답 앞부분, 해석 오류)를 남깁니다.
2. **24시간 필터**: 날짜가 없는 글은 뺍니다. 날짜 없는 글을 새 글로 보면 몇 달 전 글이 오늘 소식이 됩니다.
3. **거르기**
   - `not_news`: 출처별로 지정한 분류(`exclude_categories`)이거나, 분류가 없는 글(`drop_uncategorized`)
   - `promo`: 제목의 홍보 표현이나 `sponsored`·광고·후원 분류
   - `already_published`: 지난 브리핑과 이전 후보에 이미 실린 주소·제목(잘린 제목도 앞부분으로 잡음)
   - `duplicate_link`: 여러 피드가 같은 주소를 실었으면 가중치 높은 쪽만 남김
   - `signal_only`: `role: "signal"` 출처(Hacker News)는 싣지 않고 교차보도 신호로만 씀
4. **점수** = 출처 가중치 + 교차보도(같은 사건을 다룬 다른 출처 1곳당 1.0, 최대 3.0) + 신선도(최대 1.0)
   - 같은 사건 판정은 제목 키워드로 합니다. 한국어·영어 표기를 같은 열쇠로 모읍니다(구글/Google, 오픈AI/OpenAI 등, `pipeline/text.py` 의 `ALIASES`).
   - "구글", "제미나이"처럼 그날 여러 기사에 나온 말만 겹쳐서는 같은 사건이 아닙니다. 그날 드물게 나온 말이 하나 이상 겹쳐야 합니다.
5. **고르기**: 점수순으로 훑으며 한 매체 하루 3건, 같은 사건 1건만 고릅니다.
   - 10건을 못 채우면 매체 상한을 한 칸씩만 올리고, 몇까지 올렸는지 `source_cap_used` 에 남깁니다.
   - 같은 사건 규칙은 풀지 않습니다.
6. **원문 확인**: 고른 기사의 원문을 열어 본문 문단이 600자 이상 읽히는지 봅니다. 못 읽으면 빼고 다시 고릅니다. 본문은 저장하지 않습니다.
7. **기록**: 단계별 건수(`funnel`), 출처별 상태(`sources`), 고른 기사와 점수 내역(`selected`), 예비(`reserve`), 국내(`korea`), 교체 내역(`substitutions`), 안 뽑힌 이유(`not_selected`), 경고(`warnings`)

**출처 추가·조정**은 `data/sources.json` 에서 합니다.

| 필드 | 뜻 |
|---|---|
| `weight` | 출처 가중치 |
| `pool` | `main`(메인 10건) 또는 `korea`(국내 소식) |
| `role` | `signal` 이면 싣지 않고 교차보도 신호로만 씀 |
| `kind` | `media` / `official` / `community` |
| `timezone` | 시간대 없는 날짜를 읽을 때 쓸 시간대 (예: `+09:00`) |
| `exclude_categories`, `drop_uncategorized` | 뉴스가 아닌 글을 거르는 분류 규칙 |
| `require_ai_terms` | AI 관련 단어가 없으면 거름 |
| `enabled` | `false` 면 수집하지 않음 |

## 브리핑 JSON 형식

```jsonc
{
  "date": "2026-10-29",                 // 파일 이름과 같아야 함
  "stats": { "scanned": 1240, "candidates": 72 },   // 선택
  "insight": {
    "headline": "오늘의 한 줄",
    "body": ["문단", "문단"],           // 마지막 문단이 meta description 으로 쓰임
    "watch": "다음에 볼 것"              // 선택
  },
  "articles": [
    {
      "rank": 1,                        // 1부터 빠짐없이
      "title": "한글 제목",
      "title_original": "Original title",   // 선택
      "source": "매체 이름",
      "url": "https://...",
      "published": "2026-10-28",        // 선택
      "topic": "models",                // site.json topics 의 키
      "official": true,                 // 선택: '공식 발표' 배지
      "coverage": 4,                    // 선택: 2 이상이면 'N개 매체 보도' 배지
      "summary": "요약",
      "implication": "왜 중요한가",       // 선택: 접혀 있는 칸
      "related": [{ "source": "...", "url": "..." }]   // 선택
    }
  ],
  "korea": [{ "title": "", "summary": "", "source": "", "url": "" }]   // 선택
}
```

본문(`insight.body`, `summary`, `implication`)에 `glossary.json` 의 용어 이름이 나오면
빌드할 때 자동으로 설명 버튼이 붙습니다. 문단마다 처음 한 번만 붙습니다.

형식이 틀리면 빌드가 멈추고 어느 파일의 무엇이 문제인지 알려 줍니다.

## 작업 사례 JSON 형식

작업 하나는 `data/work/<slug>.json` 파일 하나입니다. 파일 이름(영문 소문자·숫자·하이픈)이
그대로 주소 `/work/<slug>.html` 이 됩니다. 이미지는 `static/work/<slug>/` 에 넣고
`/assets/work/<slug>/파일명` 으로 적습니다. 없는 이미지를 적으면 빌드가 멈춥니다.

```jsonc
{
  "title": "작업 이름",
  "category": "개인 프로젝트 · 웹사이트",
  "summary": "한두 문장 소개",               // 목록과 상세 머리에 쓰임
  "tagline": "한 줄 요약",                   // 선택: 상세 페이지 큰 제목
  "status": "in_progress",                   // 선택: in_progress(기본) 또는 done
  "featured": true,                          // 선택: 홈에 먼저 보임
  "order": 1,                                // 선택: 목록 순서 (없으면 최근 단계 날짜순)
  "cover": "/assets/work/<slug>/cover.jpg",  // 선택: 없으면 그라데이션 판에 제목
  "role": ["기획", "디자인", "개발"],
  "stack": ["Python", "Jinja2"],
  "links": [{ "label": "사이트 보기", "url": "https://..." }],
  "story": [                                 // 선택: 개요 문단들
    { "heading": "어떤 문제를 풀었나요?", "body": ["문단", "문단"] }
  ],
  "process": [                               // 필수: 만든 과정. 날짜순으로 자동 정렬
    {
      "date": "2026-10-29",
      "phase": "조사",                       // 조사·기획·디자인·개발·출시 등 자유롭게
      "title": "이 단계에서 한 일",
      "body": ["문단"],
      "decisions": ["결정한 것"],            // 선택
      "learned": ["배운 점"],                // 선택
      "images": [{ "src": "/assets/work/<slug>/step1.jpg", "caption": "설명" }]   // 선택
    }
  ],
  "gallery": [{ "src": "...", "caption": "..." }],   // 선택: 결과 화면
  "retrospective": { "good": ["잘된 점"], "next": ["다음에 할 것"] },   // 선택
  "disclosure": "참고 문구"                  // 선택: 페이지 맨 아래 작은 글씨
}
```

진행 중인 작업은 단계를 하나씩 `process` 에 추가하면서 기록하면 됩니다.
기간(시작~마지막)과 단계 흐름(조사 → 개발 → 디자인)은 단계 날짜로 자동 계산됩니다.

## 영어판과 번역

- **화면 문구**(메뉴, 버튼, 안내)는 `data/ui.json` 에 `ko` 와 `en` 두 벌로 있습니다.
  한쪽에만 키가 있으면 빌드가 멈추고 어느 키인지 알려 줍니다.
- **콘텐츠**는 같은 JSON 안에서 필드 이름 뒤에 `_en` 을 붙여 번역합니다. 어디에 있든 똑같이 동작합니다.

  ```jsonc
  { "title": "정적 사이트 생성기 첫 버전", "title_en": "First version of the static site generator",
    "decisions": ["…"], "decisions_en": ["…"] }
  ```

  `site.json`(tagline, description, author, topics), 브리핑(insight·articles·korea), 작업(story·process·images 등),
  용어사전(`body_en`) 모두 같은 규칙입니다. 기사 제목은 `title_en` 이 없으면 `title_original` 을 씁니다.
- **번역이 없을 때**: 브리핑은 `insight.headline_en`, 작업은 `title_en` 또는 `summary_en` 이 없으면
  "아직 번역되지 않았습니다" 안내와 함께 한국어 원문을 보여 주고, 검색엔진에는 `noindex` 로 노출하지 않습니다.
- 상단 바의 **링크 복사** 버튼은 지금 보는 페이지 주소를 복사합니다(클립보드를 쓸 수 없는 브라우저에서는 숨겨짐).
  **EN/KO** 버튼은 같은 페이지의 다른 언어판으로 가며, 보던 위치(`#a3`)와 검색어(`?q=`)를 유지합니다.

## 디자인 바꾸기

색·글꼴·간격은 `static/css/tokens.css` 에서만 바꿉니다. 다크 모드 값은 같은 파일 아래쪽에
두 번(OS 설정용, 사용자 선택용) 들어 있으니 함께 고쳐야 합니다.

## 아직 없는 것

- 후보(`data/candidates/`)에서 요약·시사점·번역을 써서 브리핑 JSON을 만드는 단계 (지금은 직접 써야 함)
- 파이프라인을 매일 정해진 시각에 돌리는 예약 실행
- 주제 분류 자동화
- 이메일 구독, OG 이미지 생성, 주간·월간 회고 페이지
- 브라우저 언어에 맞춰 다른 언어판을 권하는 안내 배너
- 배포 설정. 어떤 정적 호스팅이든 `python -m generator.build` 후 `dist/` 를 올리면 됩니다.

`data/briefings/` 의 두 호와 `data/work/example-habit-app.json` 은 레이아웃 확인용
**예시 데이터**입니다. `data/work/ai24times.json` 은 이 사이트를 만든 실제 과정입니다.
