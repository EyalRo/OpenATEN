import struct

import pytest
from hypothesis import given
from hypothesis import strategies as st

from openaten import protocol as pr


def test_cdb_is_16_bytes_and_sums_to_zero():
    for c in (pr.cdb_state(), pr.cdb_switch_ufp(2), pr.cdb_tx(), pr.cdb_rx(), pr.cdb_hid(5), pr.cdb(pr.CMD_INIT)):
        assert len(c) == 16 and c[0] == 0xF2 and sum(c) & 0xFF == 0


def test_known_state_cdb():
    # GetState, 1 byte in: checksum = -(0xF2 + 0x04 + 0x01) & 0xFF = 0x09 (matches the bytes seen on real hardware)
    assert pr.cdb_state().hex(" ") == "f2 09 04 00 00 00 00 01 00 00 00 00 00 00 00 00"


def test_tx_length_is_big_endian():
    assert pr.cdb_tx(2048)[4:8] == bytes([0, 0, 8, 0])


def test_packet_roundtrip():
    p = pr.packet(pr.T_DATA, b"hello", stream=7)
    assert len(p) == pr.PKT_LEN
    d = pr.parse(p)
    assert (d["type"], d["stream"], d["length"], d["flags"]) == (pr.T_DATA, 7, 2048, 1)
    assert d["payload"].startswith(b"hello")


def test_payload_too_large():
    with pytest.raises(ValueError):
        pr.packet(pr.T_DATA, bytes(pr.PKT_LEN))


def test_connect_fields():
    d = pr.parse(pr.connect(layout=1, conn_type=2))
    pl = d["payload"]
    assert d["type"] == pr.T_CONNECT
    assert struct.unpack_from("<I", pl, 0)[0] == 0x10000  # +0x10 max transfer
    assert struct.unpack_from("<H", pl, 4)[0] == 0x300  # +0x14
    assert struct.unpack_from("<H", pl, 6)[0] == 0x0102  # +0x16
    assert (pl[9], pl[10]) == (1, 2)  # +0x19 layout, +0x1A connection type


def test_mouse_cross_layout():
    pl = pr.parse(pr.mouse_cross(640, 360, 2))["payload"]
    assert struct.unpack_from("<HHB", pl, 0) == (640, 360, 2)


def test_hid_reports():
    assert pr.mouse_report(0, 5, 0, 0, 0) == bytes([1, 0, 0, 5, 0])  # [id, buttons, wheel, dx, dy]
    assert pr.key_report(0, 0x04) == bytes([2, 0, 4, 0, 0, 0, 0, 0])  # [id, modifiers, key1..key6]
    assert len(pr.key_report(2, 1, 2, 3, 4, 5, 6, 7)) == 8


def test_coalesce_sums_motion_within_a_button_state_and_splits_overflow():
    m = pr.mouse_report
    assert pr.coalesce_mouse([m(0, 5, 1), m(0, 2, 3), m(0, 1, 0)]) == [m(0, 8, 4)]
    assert pr.coalesce_mouse([m(0, 100, 0), m(0, 100, 0)]) == [m(0, 127, 0), m(0, 73, 0)]
    assert pr.coalesce_mouse([m(0, 0, 0, wheel=1), m(0, 0, 0, wheel=1)]) == [m(0, 0, 0, wheel=2)]


def test_coalesce_never_merges_across_a_button_change_or_other_report_kinds():
    m, k = pr.mouse_report, pr.key_report
    seq = [m(0, 3, 0), m(1, 3, 0), m(1, 2, 0), k(0, 4), m(1, 1, 0), m(0, 1, 0)]
    assert pr.coalesce_mouse(seq) == [m(0, 3, 0), m(1, 5, 0), k(0, 4), m(1, 1, 0), m(0, 1, 0)]
    assert pr.coalesce_mouse([k(0, 4), k()]) == [k(0, 4), k()]  # key presses are never dropped or merged
    assert pr.coalesce_mouse([]) == []


@given(st.lists(st.tuples(st.integers(0, 3), st.integers(-127, 127), st.integers(-127, 127), st.integers(-127, 127)), max_size=30))
def test_coalesce_preserves_total_motion_and_button_sequence(raw):
    reports = [pr.mouse_report(b, dx, dy, w) for b, dx, dy, w in raw]
    out = pr.coalesce_mouse(reports)

    def totals(rs):
        acc = {}
        for r in rs:
            t = acc.setdefault(r[1], [0, 0, 0])
            for i, v in enumerate((r[2], r[3], r[4])):
                t[i] += v - 256 if v > 127 else v
        return acc

    assert len(out) <= len(reports) or not reports
    assert totals(out) == totals(reports)
    assert [r[1] for r in dedup(out)] == [r[1] for r in dedup(reports)]  # same button-state transitions


def dedup(rs):
    out = []
    for r in rs:
        if not out or out[-1][1] != r[1]:
            out.append(r)
    return out


# -- coalescing must not change where a pointer that clamps at the screen edges ends up ---------------------------------------------
# The edge hand-off slams the other computer's pointer far past the top-left corner (the compositor clamps it there) and then moves it to the
# entry row. Merging those two into one summed report removes the clamp in between, so the pointer ends up somewhere else.


def clamped_end(reports, start, size):
    """Where a pointer starting at ``start`` ends up when every report moves it and the screen edges clamp it after each one."""
    x, y = start
    for r in reports:
        if len(r) == 5 and r[0] == pr.MOUSE_REPORT_ID:
            dx, dy = (v - 256 if v > 127 else v for v in (r[3], r[4]))
            x = min(max(x + dx, 0), size[0])
            y = min(max(y + dy, 0), size[1])
    return x, y


def test_the_edge_slam_and_the_move_to_the_entry_row_are_not_merged():
    from openaten import hid

    slam = hid.move_reports(-2300, -1400)  # far past the top-left corner of a 1920x1200 screen
    go = hid.move_reports(1900, 700)  # then to the entry point
    merged = pr.coalesce_mouse(slam + go)
    for start in [(0, 0), (960, 600), (1920, 1200), (10, 1100)]:
        assert clamped_end(merged, start, (1920, 1200)) == clamped_end(slam + go, start, (1920, 1200)) == (1900, 700)


def test_motion_that_reverses_direction_on_an_axis_is_kept_apart_but_same_direction_runs_still_merge():
    m = pr.mouse_report
    assert pr.coalesce_mouse([m(0, 100, 0), m(0, -100, 0)]) == [m(0, 100, 0), m(0, -100, 0)]
    assert pr.coalesce_mouse([m(0, 0, 5), m(0, 0, -5)]) == [m(0, 0, 5), m(0, 0, -5)]
    assert pr.coalesce_mouse([m(0, 3, 0), m(0, 0, -2), m(0, 4, 0)]) == [m(0, 7, -2)]  # a different axis moving the other way is fine
    assert pr.coalesce_mouse([m(0, 100, 0), m(0, 100, 0), m(0, -1, 0)]) == [m(0, 127, 0), m(0, 73, 0), m(0, -1, 0)]


@given(
    st.lists(st.tuples(st.integers(-127, 127), st.integers(-127, 127)), max_size=40),
    st.tuples(st.integers(0, 300), st.integers(0, 200)),
)
def test_coalescing_never_changes_where_a_clamped_pointer_ends_up(motion, start):
    reports = [pr.mouse_report(0, dx, dy) for dx, dy in motion]
    assert clamped_end(pr.coalesce_mouse(reports), start, (300, 200)) == clamped_end(reports, start, (300, 200))
