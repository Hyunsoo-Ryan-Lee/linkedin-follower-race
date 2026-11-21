# Follower Race

Databricks와 Snowflake의 LinkedIn 팔로워 비교 대시보드.

사이트: https://hyunsoo-ryan-lee.github.io/linkedin-follower-race/

GitHub Actions에서 5분 간격으로 공개 회사 페이지를 조회합니다. 개인 Mac과 Brave는 필요하지 않습니다. 실행 지연이나 LinkedIn 접근 제한으로 수집이 실패하면 마지막으로 검증된 수치와 원래 관측 시각을 유지하고 실패 상태를 표시합니다. 인증 쿠키, 프록시, 접근 제한 우회는 사용하지 않습니다.

액션 실행 성공과 새 데이터 수집 성공은 다릅니다. 각 실행 요약과 data.json의 collection.state를 확인하세요.
