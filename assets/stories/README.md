# Story episodes

One YAML file per long-form episode, used in file-name order (`01_...`, `02_...`). Each is used once; the channel
falls back to automatic fan-topic episodes when every story has been used. Format: see the docstring of
`lane/story.py`.

Writing rules (why the stories are built this way):

- **Style of the most-watched royal-family channels**: a hook with 2-3 burning questions, "stay until the end",
  a quick like/subscribe ask, then a chronological story full of concrete dates, places, numbers and emotions, one
  "tell us in the comments" question in the middle, and a warm, reflective ending. ~1,400-1,550 words = 8.5-9.5 min.
- **Drama from real events only.** Every fact must be on public record (official announcements, court judgments,
  major news). Anything uncertain is said as "reportedly" or "fans have long wondered". No invented rumours, no
  claims about anyone's private life that are not public, no faked pictures. This is what keeps the channel
  monetised and safe from strikes and legal trouble — and real royal stories are dramatic enough.
- `show` tokens decide the pictures for each part: `him`, `him:<words>`, `event:<words>`, `cat:<Wikimedia Commons
  category>`, `search:<words>`.
