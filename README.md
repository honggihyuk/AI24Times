# AI24Times

매일 아침 지난 24시간의 AI 소식을 골라 요약하는 브리핑 사이트입니다.
`data/` 의 JSON을 Jinja 템플릿으로 렌더링해 `dist/` 에 정적 HTML을 만듭니다. 서버가 필요 없습니다.

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
  site.json              사이트 이름·소개·주소·발행 시각·주제 목록
  glossary.json          용어사전 (slug → 이름·영문·설명)
  briefings/YYYY-MM-DD.json   하루치 브리핑 한 호
generator/build.py       빌드 스크립트 (데이터 검증 → 렌더링 → 정적 파일 복사)
templates/               Jinja 템플릿
  base.html.j2           공통 머리·바닥, 테마 초기화
  _macros.html.j2        기사 카드, 목록 한 줄
  daily.html.j2          오늘자 / 지난 호 한 호
  archive_index.html.j2  지난 호 목록 + 검색
  topics_index.html.j2, topic.html.j2, glossary.html.j2, feed.xml.j2
static/                  dist/assets/ 로 그대로 복사됨
  css/tokens.css         색·글꼴·크기·간격 토큰 (라이트/다크)
  css/site.css           레이아웃·컴포넌트 (토큰만 사용)
  js/site.js             테마 전환, 용어 패널, 목차 하이라이트, 지난 호 검색
tests/test_build.py
```

## 빌드 결과

| 경로 | 내용 |
|---|---|
| `/` | 가장 최근 호 |
| `/archive/YYYY-MM-DD.html` | 날짜별 호 (최신 호 포함) |
| `/archive/` | 월별 목록 + 검색 |
| `/topics/`, `/topics/<slug>.html` | 주제별 기사 모음 |
| `/glossary.html` | 용어사전 (`#slug` 로 바로 이동) |
| `/feed.xml` | RSS (최근 20호) |
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

## 디자인 바꾸기

색·글꼴·간격은 `static/css/tokens.css` 에서만 바꿉니다. 다크 모드 값은 같은 파일 아래쪽에
두 번(OS 설정용, 사용자 선택용) 들어 있으니 함께 고쳐야 합니다.

## 아직 없는 것

- 기사 수집·선정·요약 파이프라인 (지금은 JSON을 직접 넣어야 함)
- 이메일 구독, OG 이미지 생성, 영어판, 주간·월간 회고 페이지
- 배포 설정. 어떤 정적 호스팅이든 `python -m generator.build` 후 `dist/` 를 올리면 됩니다.

`data/briefings/` 의 두 호는 레이아웃 확인용 **예시 데이터**이며 실제 뉴스가 아닙니다.
