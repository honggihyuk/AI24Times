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
generator/build.py       빌드 스크립트 (데이터 검증 → 렌더링 → 정적 파일 복사)
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
tests/test_build.py
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

- 기사 수집·선정·요약 파이프라인 (지금은 JSON을 직접 넣어야 함)
- 이메일 구독, OG 이미지 생성, 주간·월간 회고 페이지
- 브라우저 언어에 맞춰 다른 언어판을 권하는 안내 배너
- 배포 설정. 어떤 정적 호스팅이든 `python -m generator.build` 후 `dist/` 를 올리면 됩니다.

`data/briefings/` 의 두 호와 `data/work/example-habit-app.json` 은 레이아웃 확인용
**예시 데이터**입니다. `data/work/ai24times.json` 은 이 사이트를 만든 실제 과정입니다.
