//! Pure-Rust port of bisdecode's parsers (src/bisdecode/decode.py).
//!
//! No JS or Python types here, so the same core could back a WASM build
//! (see wasm.rs) and, later, a PyO3 build. Behaviour mirrors pandas 3.x on the
//! parity fixtures; the edge-case rules are listed in ../../parity/SEMANTICS.md.

pub const SPECTRA_BINS: usize = 60;
const SENTINELS: [f64; 4] = [-3276.8, -3276.0, -327.7, 3276.8];
const HEX_PARAMS: [&str; 2] = ["ARTF2", "BISBIT00"];

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Kind {
    Time,
    Float,
    Int,
    Str,
}

#[derive(Debug)]
pub enum Values {
    /// Time (epoch ms, NaN = NaT), Float (NaN = missing), Int (NaN = <NA>).
    Num(Vec<f64>),
    Str(Vec<Option<String>>),
}

#[derive(Debug)]
pub struct Column {
    pub name: String,
    pub kind: Kind,
    pub values: Values,
}

#[derive(Debug)]
pub struct Table {
    pub nrows: usize,
    pub columns: Vec<Column>,
}

pub type Error = String;

// ---------- text helpers (Python semantics) ----------

/// Python `open(..., "r").read()` with UTF-8: strict, BOM kept.
pub fn decode_text(bytes: &[u8]) -> Result<&str, Error> {
    std::str::from_utf8(bytes).map_err(|e| format!("not UTF-8: {e}"))
}

/// Python str.isspace().
pub fn is_py_space(c: char) -> bool {
    matches!(c, '\t'..='\r' | '\x1c'..='\x20' | '\u{85}' | '\u{a0}' | '\u{1680}'
        | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}')
}

pub fn py_strip(s: &str) -> &str {
    s.trim_matches(is_py_space)
}

fn is_line_break(c: char) -> bool {
    matches!(c, '\n' | '\r' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}')
}

/// Python str.splitlines().
pub fn splitlines(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut it = s.char_indices().peekable();
    while let Some((i, c)) = it.next() {
        if is_line_break(c) {
            out.push(&s[start..i]);
            let mut end = i + c.len_utf8();
            if c == '\r' {
                if let Some(&(_, '\n')) = it.peek() {
                    it.next();
                    end += 1;
                }
            }
            start = end;
        }
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

/// pd.to_numeric(errors="coerce") on one string: None if it does not parse.
pub fn to_numeric(raw: &str) -> Option<f64> {
    let s = py_strip(raw);
    let b = s.as_bytes();
    let mut i = 0;
    if i < b.len() && (b[i] == b'+' || b[i] == b'-') {
        i += 1;
    }
    let body = &s[i..];
    if body.eq_ignore_ascii_case("nan") {
        return Some(f64::NAN);
    }
    if body.eq_ignore_ascii_case("inf") || body.eq_ignore_ascii_case("infinity") {
        return Some(if b[0] == b'-' { f64::NEG_INFINITY } else { f64::INFINITY });
    }
    // [+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?
    let digits = |j: &mut usize| {
        let s0 = *j;
        while *j < b.len() && b[*j].is_ascii_digit() {
            *j += 1;
        }
        *j - s0
    };
    let mut j = i;
    let int_n = digits(&mut j);
    let mut frac_n = 0;
    if j < b.len() && b[j] == b'.' {
        j += 1;
        frac_n = digits(&mut j);
    }
    if int_n == 0 && frac_n == 0 {
        return None;
    }
    if j < b.len() && (b[j] == b'e' || b[j] == b'E') {
        j += 1;
        if j < b.len() && (b[j] == b'+' || b[j] == b'-') {
            j += 1;
        }
        if digits(&mut j) == 0 {
            return None;
        }
    }
    if j != b.len() {
        return None;
    }
    s.parse::<f64>().ok()
}

/// Python int(v, 16): sign, optional 0x prefix, single '_' between digits.
pub fn hex_to_int(v: &str) -> f64 {
    let s = py_strip(v);
    if s.is_empty() {
        return f64::NAN;
    }
    let (neg, mut rest) = match s.as_bytes()[0] {
        b'-' => (true, &s[1..]),
        b'+' => (false, &s[1..]),
        _ => (false, s),
    };
    if rest.len() > 2 && (rest.starts_with("0x") || rest.starts_with("0X")) {
        rest = &rest[2..];
        if let Some(r) = rest.strip_prefix('_') {
            rest = r;
        }
    }
    let mut n: f64 = 0.0;
    let mut prev_us = true; // no leading '_'
    for c in rest.chars() {
        if c == '_' {
            if prev_us {
                return f64::NAN;
            }
            prev_us = true;
            continue;
        }
        match c.to_digit(16) {
            Some(d) => n = n * 16.0 + d as f64,
            None => return f64::NAN,
        }
        prev_us = false;
    }
    if prev_us {
        return f64::NAN; // empty, or trailing '_'
    }
    if neg { -n } else { n }
}

fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (m + if m > 2 { -3 } else { 9 }) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

/// pd.to_datetime(format="%m/%d/%Y %H:%M:%S", errors="coerce") -> epoch ms.
/// 1-2 digit fields, exactly 4-digit year, seconds 0-61 (60/61 roll over).
pub fn parse_time(s: &str) -> f64 {
    let b = s.as_bytes();
    let mut f = [0i64; 6];
    let seps = [b'/', b'/', b' ', b':', b':'];
    let mut i = 0;
    for k in 0..6 {
        let st = i;
        while i < b.len() && b[i].is_ascii_digit() {
            f[k] = f[k] * 10 + (b[i] - b'0') as i64;
            i += 1;
        }
        let n = i - st;
        let ok = if k == 2 { n == 4 } else { (1..=2).contains(&n) };
        if !ok {
            return f64::NAN;
        }
        if k < 5 {
            if i >= b.len() || b[i] != seps[k] {
                return f64::NAN;
            }
            i += 1;
        }
    }
    if i != b.len() {
        return f64::NAN;
    }
    let [mo, d, y, h, mi, se] = f;
    if y < 1 || !(1..=12).contains(&mo) || d < 1 || h > 23 || mi > 59 || se > 61 {
        return f64::NAN;
    }
    let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    let dim = [31, if leap { 29 } else { 28 }, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    if d > dim[(mo - 1) as usize] {
        return f64::NAN;
    }
    ((days_from_civil(y, mo, d) * 86400 + h * 3600 + mi * 60 + se) * 1000) as f64
}

fn is_sentinel(x: f64) -> bool {
    SENTINELS.contains(&x) || x <= -3276.0
}

// ---------- parsers ----------

/// .spa per-second processed trend -> wide table '<chan>:<param>'.
pub fn parse_spa(bytes: &[u8]) -> Result<Table, Error> {
    let text = decode_text(bytes)?;
    let lines = splitlines(text);
    if lines.len() < 2 {
        return Err(".spa needs two header rows".into());
    }
    let ch_row: Vec<&str> = lines[0].split('|').collect();
    let mut names = Vec::new();
    let mut cur_ch: Option<String> = None;
    for (i, name) in lines[1].split('|').enumerate() {
        let ch = ch_row.get(i).map(|c| py_strip(c)).unwrap_or("");
        if ch.starts_with("Ch") {
            cur_ch = Some(ch.replace(' ', ""));
        }
        let nm = py_strip(name);
        names.push(if i == 0 {
            "timestamp".to_string()
        } else if let (Some(c), false) = (&cur_ch, nm.is_empty()) {
            format!("{c}:{nm}")
        } else if nm.is_empty() {
            format!("col{i}")
        } else {
            nm.to_string()
        });
    }
    let ncol = names.len();
    let cells: Vec<Vec<&str>> = lines[2..]
        .iter()
        .filter(|ln| !py_strip(ln).is_empty())
        .map(|ln| {
            // Python: ln.split("|")[:ncol], short rows padded with ""
            let mut f: Vec<&str> = ln.splitn(ncol + 1, '|').take(ncol).collect();
            f.resize(ncol, "");
            f
        })
        .collect();
    let nrows = cells.len();
    let mut columns = Vec::with_capacity(ncol);

    for (j, name) in names.into_iter().enumerate() {
        if j == 0 {
            let v = cells.iter().map(|r| parse_time(r[0])).collect();
            columns.push(Column { name, kind: Kind::Time, values: Values::Num(v) });
            continue;
        }
        let txt: Vec<&str> = cells.iter().map(|r| py_strip(r[j])).collect();
        if name.starts_with("col") && txt.iter().all(|t| t.is_empty()) {
            continue; // trailing '|' field
        }
        let param = name.rsplit(':').next().unwrap_or("");
        if HEX_PARAMS.contains(&param) {
            let v = txt.iter().map(|t| hex_to_int(t)).collect();
            columns.push(Column { name, kind: Kind::Int, values: Values::Num(v) });
            continue;
        }
        let mut v = Vec::with_capacity(nrows);
        let mut numeric = true;
        for t in &txt {
            if t.is_empty() {
                v.push(f64::NAN);
                continue;
            }
            match to_numeric(t) {
                Some(x) if !x.is_nan() => v.push(if is_sentinel(x) { f64::NAN } else { x }),
                _ => {
                    numeric = false;
                    break;
                }
            }
        }
        if numeric {
            columns.push(Column { name, kind: Kind::Float, values: Values::Num(v) });
        } else {
            let s = txt.iter().map(|t| Some(t.to_string())).collect();
            columns.push(Column { name, kind: Kind::Str, values: Values::Str(s) });
        }
    }
    Ok(Table { nrows, columns })
}

/// .f_a density spectral array -> timestamp + bin00..binNN.
pub fn parse_spectra(bytes: &[u8]) -> Result<Table, Error> {
    let text = decode_text(bytes)?;
    let lines = splitlines(text);
    let mut rows: Vec<(&str, Vec<&str>)> = Vec::new();
    for ln in lines.iter().skip(2) {
        if py_strip(ln).is_empty() || !ln.contains('|') {
            continue;
        }
        let mut fields = ln.split('|');
        let ts = fields.next().unwrap_or("");
        let mut bins: Vec<&str> = fields.next().unwrap_or("").split(',').collect();
        if bins.last().map_or(false, |b| py_strip(b).is_empty()) {
            bins.pop(); // trailing comma only
        }
        rows.push((ts, bins));
    }
    let n_bins = rows.iter().map(|r| r.1.len()).max().unwrap_or(0).max(SPECTRA_BINS);
    let nrows = rows.len();
    let ts: Vec<f64> = rows.iter().map(|r| parse_time(r.0)).collect();
    let mut columns = vec![Column { name: "timestamp".into(), kind: Kind::Time, values: Values::Num(ts) }];
    for b in 0..n_bins {
        let v = rows
            .iter()
            .map(|r| r.1.get(b).and_then(|s| to_numeric(s)).unwrap_or(f64::NAN))
            .collect();
        columns.push(Column { name: format!("bin{b:02}"), kind: Kind::Float, values: Values::Num(v) });
    }
    Ok(Table { nrows, columns })
}

/// 'MM/DD/YYYY HH:MM:SS' (two-digit fields, ASCII digits) at the start of `s`.
fn ts_prefix(s: &str) -> Option<(&str, &str)> {
    let b = s.as_bytes();
    let pat = b"dd/dd/dddd dd:dd:dd";
    if b.len() < pat.len() {
        return None;
    }
    for (c, p) in b.iter().zip(pat) {
        let ok = if *p == b'd' { c.is_ascii_digit() } else { c == p };
        if !ok {
            return None;
        }
    }
    Some(s.split_at(pat.len()))
}

fn skip_ws(s: &str) -> (&str, usize) {
    let t = s.trim_start_matches(is_py_space);
    (t, s.len() - t.len())
}

fn is_word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// `\s+\(ID (\d+)\)\s*$` on the whole of `s`; returns the id.
fn id_suffix(s: &str) -> Option<&str> {
    let (r, n) = skip_ws(s);
    if n == 0 {
        return None;
    }
    let r = r.strip_prefix("(ID ")?;
    let d = r.len() - r.trim_start_matches(|c: char| c.is_ascii_digit()).len();
    if d == 0 {
        return None;
    }
    let tail = r[d..].strip_prefix(')')?;
    if !tail.chars().all(is_py_space) {
        return None;
    }
    Some(&r[..d])
}

/// EVENT_RE: ts \s+ > \s+ (\w+) \s+ (.*?) (?:\s+\(ID (\d+)\))? \s*$
fn match_event(line: &str) -> Option<(&str, &str, &str, Option<&str>)> {
    let (ts, rest) = ts_prefix(line)?;
    let (rest, n) = skip_ws(rest);
    if n == 0 {
        return None;
    }
    let rest = rest.strip_prefix('>')?;
    let (rest, n) = skip_ws(rest);
    if n == 0 {
        return None;
    }
    let kind_len = rest.len() - rest.trim_start_matches(is_word).len();
    if kind_len == 0 {
        return None;
    }
    let (kind, rest) = rest.split_at(kind_len);
    let (body, n) = skip_ws(rest);
    if n == 0 {
        return None;
    }
    // lazy msg: the first cut where the remainder is an ID suffix or blank
    for (p, _) in body.char_indices().chain(std::iter::once((body.len(), ' '))) {
        let tail = &body[p..];
        if let Some(id) = id_suffix(tail) {
            return Some((ts, kind, &body[..p], Some(id)));
        }
        if tail.chars().all(is_py_space) {
            return Some((ts, kind, &body[..p], None));
        }
    }
    None
}

/// .e_a events -> timestamp, kind, msg, id (id kept as text, like Python).
pub fn parse_events(bytes: &[u8]) -> Result<Table, Error> {
    let text = decode_text(bytes)?;
    let (mut ts, mut kind, mut msg, mut id) = (vec![], vec![], vec![], vec![]);
    for ln in splitlines(text) {
        if let Some((t, k, m, i)) = match_event(py_strip(ln)) {
            ts.push(parse_time(t));
            kind.push(Some(k.to_string()));
            msg.push(Some(m.to_string()));
            id.push(i.map(str::to_string));
        }
    }
    Ok(Table {
        nrows: ts.len(),
        columns: vec![
            Column { name: "timestamp".into(), kind: Kind::Time, values: Values::Num(ts) },
            Column { name: "kind".into(), kind: Kind::Str, values: Values::Str(kind) },
            Column { name: "msg".into(), kind: Kind::Str, values: Values::Str(msg) },
            Column { name: "id".into(), kind: Kind::Str, values: Values::Str(id) },
        ],
    })
}

/// `\s*\t\s*(\d+)`: a whitespace run holding a tab, then digits.
fn tab_digits(s: &str) -> Option<(&str, &str)> {
    let (r, n) = skip_ws(s);
    if !s[..n].contains('\t') {
        return None;
    }
    let d = r.len() - r.trim_start_matches(|c: char| c.is_ascii_digit()).len();
    if d == 0 {
        return None;
    }
    Some(r.split_at(d))
}

/// .o_a raw-EEG offset index -> timestamp (text, as in Python), offset, size.
pub fn parse_offset(bytes: &[u8]) -> Result<Table, Error> {
    let text = decode_text(bytes)?;
    let (mut ts, mut off, mut size) = (vec![], vec![], vec![]);
    for ln in splitlines(text) {
        let ln = ln.trim_end_matches('\r');
        let Some((t, rest)) = ts_prefix(ln) else { continue };
        let Some((o, rest)) = tab_digits(rest) else { continue };
        let Some((z, _)) = tab_digits(rest) else { continue };
        ts.push(Some(t.to_string()));
        off.push(o.parse::<f64>().unwrap_or(f64::NAN));
        size.push(z.parse::<f64>().unwrap_or(f64::NAN));
    }
    Ok(Table {
        nrows: ts.len(),
        columns: vec![
            Column { name: "timestamp".into(), kind: Kind::Str, values: Values::Str(ts) },
            Column { name: "offset".into(), kind: Kind::Int, values: Values::Num(off) },
            Column { name: "size".into(), kind: Kind::Int, values: Values::Num(size) },
        ],
    })
}

/// .t_a case start time (raw text, stripped).
pub fn parse_start(bytes: &[u8]) -> Result<String, Error> {
    Ok(py_strip(decode_text(bytes)?).to_string())
}

/// .r2a raw EEG: int16 little-endian, n_channels interleaved.
pub fn parse_eeg(bytes: &[u8], n_channels: usize) -> Vec<i16> {
    let n = (bytes.len() / 2 / n_channels) * n_channels;
    bytes[..n * 2]
        .chunks_exact(2)
        .map(|c| i16::from_le_bytes([c[0], c[1]]))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn helpers() {
        assert_eq!(splitlines("a\r\nb\rc\n"), vec!["a", "b", "c"]);
        assert_eq!(hex_to_int("0x80"), 128.0);
        assert_eq!(hex_to_int("1_0"), 16.0);
        assert!(hex_to_int("_1").is_nan());
        assert_eq!(to_numeric("5."), Some(5.0));
        assert_eq!(to_numeric("-inf"), Some(f64::NEG_INFINITY));
        assert_eq!(to_numeric("1e"), None);
        assert_eq!(parse_time("01/02/2030 08:00:00"), 1893571200000.0);
        assert!(parse_time("02/30/2030 08:00:00").is_nan());
        let e = match_event("01/02/2030 08:00:01  >  ALARM     SQI LOW (ID 10010)").unwrap();
        assert_eq!((e.1, e.2, e.3), ("ALARM", "SQI LOW", Some("10010")));
    }
}
