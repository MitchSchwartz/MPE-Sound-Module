"""SooperLooper per-loop state codes (OSC /sl/#/get state)."""

SL_STATE_OFF = 0
SL_STATE_WAIT_START = 1
SL_STATE_RECORDING = 2
SL_STATE_WAIT_STOP = 3
SL_STATE_PLAYING = 4
SL_STATE_OVERDUBBING = 5
SL_STATE_MULTIPLYING = 6
SL_STATE_INSERTING = 7
SL_STATE_MUTE = 10
ACTIVE_PLAY = frozenset(
    {SL_STATE_PLAYING, SL_STATE_OVERDUBBING, SL_STATE_MULTIPLYING, SL_STATE_INSERTING}
)
SL_STATE_PAUSED = 14
SL_STATE_OFF_MUTED = 20

ACTIVE_RECORD = frozenset({SL_STATE_RECORDING, SL_STATE_WAIT_STOP})
QUANTIZE_WAIT = frozenset({SL_STATE_WAIT_START, SL_STATE_WAIT_STOP})

#: The loop holds no audio. BOTH codes mean empty, and the second one is the
#: trap: `stop_all_loops` sends `mute_on` to /sl/-1, so every loop that was
#: never recorded reports 20 (OFF_MUTED) rather than 0 afterwards. Testing
#: `!= SL_STATE_OFF` therefore counts fourteen empty pads as occupied the
#: moment Stop All is pressed — which is exactly enough to keep a grid alive
#: forever once anything depends on occupancy. Ask this set, never a code.
EMPTY_STATES = frozenset({SL_STATE_OFF, SL_STATE_OFF_MUTED})
