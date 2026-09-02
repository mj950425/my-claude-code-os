---
name: catalog-audit-report
description: 카탈로그 감사의 공통 큐와 정책 질문을 검색·필터 가능한 정적 HTML 보고서로 만든다. "감사 HTML", "골든셋 report", "카탈로그 리포트 만들어" 요청에서 사용한다.
---

# 카탈로그 감사 HTML 보고서

```bash
python3 .claude/os/engine/scripts/render_catalog_report.py --profile '<profile.json>'
```

보고서는 프로필의 대상·속성·신호 표시명을 사용한다. 렌더러에 성별 라벨이나 가방 규칙을
추가하지 않는다. 상단에서 입력 수, 중복 제거 검토 수, 사람 판정률, 가장 큰 신호를 확인하고,
검토 큐에서 신호 필터와 상품 검색을 실제로 동작시켜 본다.

정책 질문의 의미를 풀어야 할 때는 공유 `catalog-golden-adjudicator` 서브에이전트를 사용한다.
