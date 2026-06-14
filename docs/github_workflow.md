# GitHub 작업 흐름

GitHub은 사고 과정의 기록이다. Issue, 커밋, PR, 리뷰 코멘트는 한국어로
작성하고, 다음 네 가지가 드러나야 한다.

1. 왜 하는가
2. 어떤 판단을 했는가
3. 무엇을 했는가
4. 어떻게 검증했는가

## 원칙

- 모든 작업은 Issue에서 시작한다.
- 코드 작성 전에 Issue 코멘트로 설계 판단을 먼저 남긴다.
- main에 직접 커밋하지 않는다. 반드시 main에서 작업 브랜치를 만든다.
- 커밋은 하나의 논리적 변경만 담는다.
- PR은 Issue와 연결하고 검증 결과를 포함한다.
- GitHub에 남는 텍스트에는 특정 AI 도구명, 자동 생성 서명, 내부 도구 이름을
  쓰지 않는다.
- 내부 기획이나 아키텍처 문서는 파일명 대신 "기획서"로만 지칭한다.

## 1. Issue 생성

목적은 "왜 이 작업을 하는가"를 남기는 것이다.

권장 형식:

```powershell
gh issue create --title "명확한 한 줄 요약" --body "배경/문제 상황, 목표, 완료 조건" --label docs
```

본문에는 다음을 포함한다.

- 배경/문제 상황
- 목표
- 완료 조건

라벨은 작업 성격에 맞게 사용한다.

- `feature`
- `fix`
- `refactor`
- `docs`
- `chore`

저장소에 라벨이 없으면 먼저 생성한다.

```powershell
gh label create docs --description "문서 작업" --color 0075ca
```

## 2. 판단 과정 기록

코드를 작성하기 전에 Issue 코멘트로 기술적 판단을 남긴다.

```powershell
gh issue comment <번호> --body "어떤 접근을 선택했고, 왜 그렇게 판단했는지 기록"
```

코멘트에는 다음을 남긴다.

- 선택한 접근
- 선택한 이유
- 버린 대안과 이유
- 검증 계획
- 범위에서 제외하는 일

PR을 열기 전에 Issue만 읽어도 구현 맥락이 이해되어야 한다.

## 3. 브랜치 생성

반드시 main에서 분기한다.

```powershell
git switch main
git pull --ff-only
git switch -c "feature/#12-short-description"
```

브랜치 이름은 다음 형식을 사용한다.

- `feature/#이슈번호-설명`
- `fix/#이슈번호-설명`
- `refactor/#이슈번호-설명`
- `docs/#이슈번호-설명`
- `chore/#이슈번호-설명`

로컬 main이 원격 main보다 앞서 있으면 새 PR 범위가 섞일 수 있다. 이 경우에는
먼저 main 상태를 정리하거나, 원격 main 기준으로 별도 브랜치를 만들어 PR 범위를
작게 유지한다.

## 4. 커밋

Conventional Commits 형식을 사용한다.

```text
feat: 설명
fix: 설명
refactor: 설명
docs: 설명
chore: 설명
```

커밋 메시지는 무엇을 왜 바꿨는지 드러나야 한다. 예:

```text
docs: GitHub 작업 흐름 문서화
```

커밋 전 최소 검증:

```powershell
uv run pytest
uv run ruff check
```

타입이나 전체 품질에 영향이 있으면 추가로 실행한다.

```powershell
uv run ty check
.\scripts\check.ps1
```

## 5. PR 생성

PR은 Issue와 연결한다.

```powershell
gh pr create --title "제목" --body "본문"
```

PR 본문 템플릿:

```markdown
## 요약
Closes #이슈번호
한 줄 요약.

## 변경 사항
- 파일별 변경 내용

## 기술적 판단
- 왜 이 방식을 선택했는지
- 대안은 무엇이었는지

## 검증
- 테스트 결과
- 수동 검증 내용
```

## 6. 리뷰와 수정

PR 생성 후에는 리뷰 관점으로 점검한다.

기본 검토 관점:

- 코드 정확성
- 단순화 가능성
- 테스트 누락
- 조용히 실패할 수 있는 경로
- 타입/데이터 모델 설계
- 보안 영향

수정 전에 PR 코멘트로 선별 사유를 남긴다.

- 반영할 항목: 무엇을 왜 수용하는지
- 반영하지 않을 항목: 왜 현재 PR 범위에서 제외하는지
- 범위 밖 항목: 별도 Issue로 분리

범위 밖 피드백은 새 Issue로 추적한다.

```powershell
gh issue create --title "리뷰 후속 작업 요약" --body "PR #번호 리뷰에서 발견: ..." --label fix
```

수정은 별도 커밋으로 남긴다.

```text
fix: 리뷰 피드백 반영
```

## 7. 머지와 정리

셀프 리뷰와 검증이 끝난 뒤 머지한다.

```powershell
gh pr merge --squash
```

머지 후 main으로 돌아와 브랜치를 정리한다.

```powershell
git switch main
git pull --ff-only
git branch -d "feature/#12-short-description"
git push origin --delete "feature/#12-short-description"
```

## 작업 체크리스트

1. Issue 확인 또는 생성
2. Issue 코멘트에 판단 과정 기록
3. main에서 작업 브랜치 생성
4. 코드 또는 문서 작성
5. `uv run pytest`와 `uv run ruff check` 실행
6. 논리 단위로 커밋
7. PR 생성, `Closes #번호` 포함
8. 리뷰 점검과 선별 사유 코멘트 작성
9. 필요 시 수정 커밋 추가
10. 머지 후 로컬/리모트 브랜치 정리

## 금지 표현

GitHub Issue, PR, 커밋 메시지, 리뷰 코멘트에는 다음을 남기지 않는다.

- 특정 AI 도구명
- 자동 생성 서명
- 내부 도구나 플러그인 이름
- 내부 기획 문서의 실제 파일명
- 비밀값, 지갑 비밀번호, private key, API secret

실제 사용하는 도구를 언급해야 할 때는 일반 명칭으로 쓴다.

- "타입체커"
- "포맷터"
- "테스트 러너"
- "코드 리뷰"
- "기획서"
