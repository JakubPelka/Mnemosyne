"""Versioned local lexicons used by candidate-term quality checks."""

POLISH_STOPWORDS = frozenset(
    """
    a aby albo ale ani aż bardziej bez bo być by było będzie ci co coś czy dla do gdy gdzie
    go i ich im inna inne jeszcze jak jaka jakie jaki jako jest jeśli już kiedy kto która które
    który ma mieć mi mnie może na nad nam nas nie niż o od oraz po pod potem przez przy są się
    ta tak także tam te tego tej ten teraz to tu tych tym tylko w we więc więcej z za ze że
    """.split()
)

SWEDISH_STOPWORDS = frozenset(
    """
    alla allt att av blev bli blir då den det detta du eller en ett fler från för ha hade han
    har hon hur här i inte jag kan med men mer mot mycket när och om på samma ska sig sin sina
    som till under upp ur vad var vara vi vid vilken vilka vill än är även över
    """.split()
)

ENGLISH_STOPWORDS = frozenset(
    """
    a about after again against all also am an and any are as at be because been before being
    between both but by can could did do does doing down during each few for from further had
    has have having he her here hers herself him himself his how i if in into is it its itself
    just me more most my myself no nor not now of off on once only or other our ours ourselves
    out over own same she should so some such than that the their theirs them themselves then
    there these they this those through to too under until up very was we were what when where
    which while who whom why will with would you your yours yourself yourselves
    """.split()
)

STOPWORDS_BY_LANGUAGE = {
    "pl": POLISH_STOPWORDS,
    "sv": SWEDISH_STOPWORDS,
    "en": ENGLISH_STOPWORDS,
}

ALL_STOPWORDS = frozenset().union(*STOPWORDS_BY_LANGUAGE.values())

EXPORT_ARTIFACTS = frozenset(
    {
        "assistant",
        "cite",
        "filecite",
        "message",
        "ref",
        "search",
        "source",
        "system",
        "tool",
        "turn",
        "user",
    }
)
