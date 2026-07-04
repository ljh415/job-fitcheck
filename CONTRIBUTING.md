# Contributing to Job FitCheck

## 브랜치 전략

**GitHub Flow** 방식을 따릅니다.

- `main` 브랜치는 항상 배포 가능한 상태를 유지합니다
- 모든 작업은 브랜치에서 진행 후 PR로 머지합니다

**브랜치 네이밍:**

```
feat/calendar-integration
fix/wanted-scraper-response
chore/upgrade-httpx
```

## 커밋 컨벤션

[Conventional Commits](https://www.conventionalcommits.org/) 형식을 따릅니다.

```
type(scope): 설명
```

**타입:**

| 타입 | 용도 |
|------|------|
| `feat` | 새 기능 추가 |
| `fix` | 버그 수정 |
| `docs` | 문서 수정 |
| `style` | 코드 동작에 영향 없는 포맷·스타일 변경 |
| `refactor` | 기능 변화 없는 코드 개선 |
| `chore` | 빌드·의존성·설정 변경 |

**스코프 (선택):**

`backend`, `frontend`, `scraper`, `llm`, `docker`

`docs`처럼 프로젝트 전체에 해당하는 변경은 scope 생략 가능합니다.

**예시:**

```
feat(scraper): 리멤버 커리어 URL 파싱 지원 추가
fix(frontend): Q&A 한국어 IME 중복 입력 버그 수정
docs: CHANGELOG v0.12.0 업데이트
chore(deps): httpx 버전 업그레이드
refactor(llm): 클라이언트 싱글톤 캐시 적용
style(frontend): 비교 테이블 기술스택 칩 레이아웃 개선
```

## 버저닝

`MAJOR.MINOR.PATCH` 형식을 따릅니다.

| 버전 | 기준 |
|------|------|
| PATCH | 버그 수정, 텍스트·스타일 소폭 수정 |
| MINOR | 기능 추가 / 기능 개선 |
| MAJOR (`1.0.0`) | Phase 3 완료 — 모바일 반응형 + 다크모드 + 마크다운 편집기 개선 |

릴리즈 시 `CHANGELOG.md`를 업데이트하고 git 태그를 붙입니다.

```bash
git tag -a v0.12.0 -m "v0.12.0 — 변경 요약"
git push origin v0.12.0
```
