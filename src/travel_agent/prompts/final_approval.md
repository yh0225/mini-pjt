The traveller was shown a finished day-by-day itinerary and replied (usually in Korean).
Turn the reply into a decision:

- `approve`: true if they accept the itinerary as it is ("좋아", "이대로 저장해줘", "완벽해").
- `revise_days`: day numbers they want changed ("2일차 오후 바꿔줘" -> [2],
  "첫날이랑 마지막 날" -> [1, N]). Only numbers between 1 and the number of days.
- `all_days`: true if they want the whole plan redone ("전체 다시 짜줘", "처음부터 다시").
- `request`: what they want changed, as a short instruction in Korean
  ("2일차 오후를 실내 위주로 바꿔 주세요"). Empty when approving.

If they ask for changes without naming a day, decide which days the change affects from the
itinerary shown; if it affects every day, set `all_days`. If the reply is unclear, set
approve=false with no days and an empty request.
