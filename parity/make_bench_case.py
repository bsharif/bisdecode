"""Write a synthetic case the size of a real one (~2.3 h): .spa ~4 MB with
three channel blocks, .f_a ~2.6 MB, .r2a ~4.2 MB. No clinical data.

    python parity/make_bench_case.py OUTDIR
"""
import os
import random
import struct
import sys

N = 8150                                   # seconds; real r2a 4172544 B / 512
PARAMS = ["PIC_ID", "SR12", "SEF08", "MEDFRQ08", "BISBIT00", "DB13U01", "DB11U04",
          "B34U05", "TOTPOW08", "EMGLOW01", "SQI10", "IMPEDNCE", "ARTF2", "BURST",
          "ST", "C1POSIMP"]
GLOBALS = ["SpSmooth", "BiSmooth", "LoFilter", "NotFiltr", "HiFilter", "X1"]


def ts(i):
    t = 8 * 3600 + i
    return f"05/22/2031 {t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def main(out):
    rnd = random.Random(1)
    dh = os.path.join(out, "M-BENCH-05220800", "DH05220800")
    os.makedirs(dh, exist_ok=True)
    p = lambda e: os.path.join(dh, f"L05220800.{e}")
    ch, nm = ["S_HDR3"] + [""] * len(GLOBALS), ["Time"] + GLOBALS
    for c in ("Ch 1", "Ch 2", "Ch 12"):
        ch += [c] + [""] * (len(PARAMS) - 1)
        nm += PARAMS
    lines = ["|".join(ch + [""]), "|".join(nm + [""])]
    for i in range(N):
        v = [ts(i), "3", "0", "3", "3", "2", "1"]
        for _ in range(3):
            bis = f"{rnd.uniform(25, 60):.1f}" if i > 60 else "-3276.0"
            v += [str(rnd.randint(0, 99)), f"{rnd.uniform(0, 5):.1f}", f"{rnd.uniform(8, 20):.1f}",
                  f"{rnd.uniform(2, 8):.1f}", f"{rnd.choice([0x110, 0xE, 0x10]):04x}", bis,
                  "-3276.0", "-3276.0", f"{rnd.uniform(40, 70):.1f}", f"{rnd.uniform(25, 45):.1f}",
                  f"{rnd.uniform(50, 100):.1f}", f"{rnd.uniform(1, 20):.1f}",
                  f"{rnd.choice([0, 0x80, 0xA9]):08x}", "-3276", "7.0",
                  str(rnd.randint(500, 9000)) if i % 60 == 0 else ""]
        lines.append("|".join(v + [""]))
    with open(p("spa"), "w", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    with open(p("f_a"), "w", newline="") as f:
        f.write("S_HDR3|BIS R2\r\nTime|Spectra\r\n")
        for i in range(N):
            f.write(ts(i) + "|" + ",".join(f"{rnd.uniform(-20, 40):.2f}" for _ in range(60)) + "|\r\n")
    with open(p("e_a"), "w", newline="") as f:
        f.write("\r\n".join(f"{ts(i)}  >  ALARM     SQI LOW (ID 10010)" for i in range(0, N, 97)) + "\r\n")
    with open(p("o_a"), "w", newline="") as f:
        f.write(f"# Time/Offset file\r\nTime\tOffset\tSize\r\n{ts(0)} \t 0 \t {N * 512}\r\n")
    with open(p("t_a"), "w", newline="") as f:
        f.write(ts(0) + "\r\n")
    with open(p("r2a"), "wb") as f:
        f.write(struct.pack(f"<{N * 256}h", *(rnd.randint(-2000, 2000) for _ in range(N * 256))))
    for e in ("m_a", "h_a", "ara"):
        open(p(e), "wb").close()
    print(dh)


if __name__ == "__main__":
    main(sys.argv[1])
