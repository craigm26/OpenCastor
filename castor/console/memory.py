"""Recall over HTTP — the same ranked answer `castor memory recall` prints.

ONE RANKER, TWO SURFACES. The CLI and this endpoint both call
``castor.brain.memory_recall.recall`` and neither re-implements a line of it.
A phone that asks the robot what it remembers about the garage must get the
same list, in the same order, as the operator standing at the robot's shell —
two rankers would drift, and the drift would show up as the robot appearing to
remember different things depending on who asked.

IT ANSWERS 200 WHEN IT CANNOT ANSWER. A cold Ollama, an unpulled embed model,
a store written before embedding existed: none of those are errors on the
caller's side, and none of them are the console being down. They are the honest
answer "no recall right now", so they come back as an empty ``results`` with
``embedder: false`` and a ``detail`` line that names the reason — never a 500,
and never a bare empty list that looks like "the robot remembers nothing".

AND IT IS BOUNDED. ``recall`` carries a hard wall-clock budget
(``RECALL_DEADLINE_S``, longer than the chat path's 1.5 s because a caller who
asked a question will wait out a cold model load, but finite): a model daemon
that answers one byte at a time cannot hold a console worker open indefinitely.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from castor.brain.memory_recall import memory_file, recall

router = APIRouter()

#: The same cap the chat rail uses, for the same reason — and a ceiling here
#: too, so a caller cannot ask a Pi to rank and serialise its whole store.
MAX_K = 25


@router.get("/memory/recall")
def memory_recall(q: str = "", k: int = 5) -> dict:
    """Memories that bear on *q*, ranked by meaning and weighted by belief.

    ``score = cosine * (0.5 + 0.5 * confidence)`` over the entry's DECAYED
    confidence; a memory is only a candidate if its raw cosine clears the
    relevance floor. See ``castor.brain.memory_recall`` for the whole formula
    and the measurement the floor comes from.
    """
    query = q.strip()
    if not query:
        raise HTTPException(status_code=422, detail="q is required")
    if not 1 <= k <= MAX_K:
        raise HTTPException(status_code=422, detail=f"k must be between 1 and {MAX_K}")
    result = recall(query, k=k)
    payload = result.to_dict()
    # The NAME, not the path. A caller needs to know which store answered —
    # robot-memory.md and not some other file — and nothing on the phone can act
    # on ``/home/pi/rover/robot-memory.md``. Handing out the robot's directory
    # layout over a bearer-gated LAN endpoint is a free map for anyone who gets
    # the token, and it buys the legitimate caller nothing.
    payload["memory_file"] = memory_file().name
    return payload
