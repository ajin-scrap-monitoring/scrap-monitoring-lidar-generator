"""Length-prefixed TCP framing for MessagePack bodies."""

DEFAULT_MAX_MESSAGE_BODY_BYTES = 1_048_576
MAX_FRAME_BODY_BYTES = 4_294_967_295
FRAME_PREFIX_BYTES = 4


class FrameError(ValueError):
    """A frame limit or byte stream violates the transport contract."""


def encode_frame(
    body: bytes,
    *,
    max_body_bytes: int = DEFAULT_MAX_MESSAGE_BODY_BYTES,
) -> bytes:
    """Prefix one non-empty body with its unsigned big-endian length."""
    limit = validate_max_body_bytes(max_body_bytes)
    if not isinstance(body, bytes) or not body:
        raise FrameError("frame body must be non-empty bytes")
    if len(body) > limit:
        raise FrameError(f"frame body exceeds the {limit}-byte limit")
    return len(body).to_bytes(FRAME_PREFIX_BYTES, "big") + body


def validate_frame(
    frame: bytes,
    *,
    max_body_bytes: int = DEFAULT_MAX_MESSAGE_BODY_BYTES,
) -> None:
    """Validate one complete length-prefixed frame without copying its body."""
    limit = validate_max_body_bytes(max_body_bytes)
    if not isinstance(frame, bytes) or len(frame) <= FRAME_PREFIX_BYTES:
        raise FrameError("frame must contain a prefix and non-empty body")
    declared_body_bytes = int.from_bytes(frame[:FRAME_PREFIX_BYTES], "big")
    if declared_body_bytes != len(frame) - FRAME_PREFIX_BYTES:
        raise FrameError("frame length prefix must match its body")
    if declared_body_bytes > limit:
        raise FrameError(f"frame body exceeds the {limit}-byte limit")


class FrameDecoder:
    """Incrementally recover complete bodies from one TCP connection."""

    __slots__ = ("_buffer", "_expected_body_bytes", "_failed", "_max_body_bytes")

    def __init__(self, *, max_body_bytes: int = DEFAULT_MAX_MESSAGE_BODY_BYTES) -> None:
        self._max_body_bytes = validate_max_body_bytes(max_body_bytes)
        self._buffer = bytearray()
        self._expected_body_bytes: int | None = None
        self._failed = False

    @property
    def pending_bytes(self) -> int:
        """Return bytes retained for the incomplete frame."""
        return len(self._buffer)

    @property
    def expected_body_bytes(self) -> int | None:
        """Return the current declared body size after a complete prefix."""
        return self._expected_body_bytes

    @property
    def failed(self) -> bool:
        """Return whether this connection stream must be discarded."""
        return self._failed

    def feed(self, data: bytes) -> tuple[bytes, ...]:
        """Consume received bytes and return every newly completed body."""
        if self._failed:
            raise FrameError("frame decoder failed; reset it for a new connection")
        if not isinstance(data, bytes):
            raise FrameError("frame data must be bytes")
        self._buffer.extend(data)
        bodies: list[bytes] = []
        try:
            while True:
                if self._expected_body_bytes is None:
                    if len(self._buffer) < FRAME_PREFIX_BYTES:
                        break
                    declared_size = int.from_bytes(self._buffer[:FRAME_PREFIX_BYTES], "big")
                    del self._buffer[:FRAME_PREFIX_BYTES]
                    if declared_size == 0:
                        raise FrameError("frame body length must be greater than zero")
                    if declared_size > self._max_body_bytes:
                        raise FrameError(
                            f"frame body length exceeds the {self._max_body_bytes}-byte limit"
                        )
                    self._expected_body_bytes = declared_size

                if len(self._buffer) < self._expected_body_bytes:
                    break
                body_size = self._expected_body_bytes
                bodies.append(bytes(self._buffer[:body_size]))
                del self._buffer[:body_size]
                self._expected_body_bytes = None
        except FrameError:
            self._buffer.clear()
            self._expected_body_bytes = None
            self._failed = True
            raise
        return tuple(bodies)

    def reset(self) -> None:
        """Discard partial state when opening a new TCP connection."""
        self._buffer.clear()
        self._expected_body_bytes = None
        self._failed = False


def validate_max_body_bytes(value: int) -> int:
    """Validate and return a body limit representable by the frame prefix."""
    if type(value) is not int or not 1 <= value <= MAX_FRAME_BODY_BYTES:
        raise FrameError(f"max body bytes must be an integer in [1, {MAX_FRAME_BODY_BYTES}]")
    return value
