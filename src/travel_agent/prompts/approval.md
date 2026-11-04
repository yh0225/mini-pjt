The traveller was shown a numbered list of candidate places and replied (usually in Korean).
Turn the reply into a decision:

- `remove`: list numbers the traveller wants to drop ("3번 빼줘" -> [3], "2, 5 제외" -> [2, 5]).
- `add_requests`: things the traveller wants added, as short search phrases
  ("해변 하나 더" -> ["아이와 갈 만한 해변"], "카페도 넣어줘" -> ["분위기 좋은 카페"]).
  Keep the traveller's intent; you may make the phrase a little more specific.
- `approve`: true if the traveller accepts the list (with the removals applied) and has
  nothing to add ("좋아", "이대로 진행", "3번만 빼고 진행"). False if they asked to add places.

Only use numbers that appear in the list. If the reply is unclear, set approve=true with no
changes only when it is clearly positive; otherwise return approve=false with no changes.
