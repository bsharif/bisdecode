//! wasm-bindgen layer: same shapes as the TS port (ts/src/bisdecode.ts).
use crate::core::{self, Kind, Table, Values};
use js_sys::{Array, Float64Array, Int16Array, Object, Reflect};
use wasm_bindgen::prelude::*;

fn set(o: &Object, k: &str, v: &JsValue) {
    Reflect::set(o, &JsValue::from_str(k), v).unwrap();
}

fn to_js(t: Table) -> JsValue {
    let cols = Array::new();
    for c in t.columns {
        let o = Object::new();
        set(&o, "name", &JsValue::from_str(&c.name));
        let kind = match c.kind {
            Kind::Time => "time",
            Kind::Float => "float",
            Kind::Int => "int",
            Kind::Str => "string",
        };
        set(&o, "kind", &JsValue::from_str(kind));
        let values: JsValue = match c.values {
            Values::Num(v) => Float64Array::from(&v[..]).into(),
            Values::Str(v) => v
                .iter()
                .map(|s| s.as_deref().map_or(JsValue::NULL, JsValue::from_str))
                .collect::<Array>()
                .into(),
        };
        set(&o, "values", &values);
        cols.push(&o);
    }
    let o = Object::new();
    set(&o, "nrows", &JsValue::from_f64(t.nrows as f64));
    set(&o, "columns", &cols);
    o.into()
}

fn table(r: Result<Table, core::Error>) -> Result<JsValue, JsError> {
    r.map(to_js).map_err(|e| JsError::new(&e))
}

#[wasm_bindgen(js_name = parseSpa)]
pub fn parse_spa(bytes: &[u8]) -> Result<JsValue, JsError> {
    table(core::parse_spa(bytes))
}

#[wasm_bindgen(js_name = parseSpectra)]
pub fn parse_spectra(bytes: &[u8]) -> Result<JsValue, JsError> {
    table(core::parse_spectra(bytes))
}

#[wasm_bindgen(js_name = parseEvents)]
pub fn parse_events(bytes: &[u8]) -> Result<JsValue, JsError> {
    table(core::parse_events(bytes))
}

#[wasm_bindgen(js_name = parseOffset)]
pub fn parse_offset(bytes: &[u8]) -> Result<JsValue, JsError> {
    table(core::parse_offset(bytes))
}

#[wasm_bindgen(js_name = parseStart)]
pub fn parse_start(bytes: &[u8]) -> Result<String, JsError> {
    core::parse_start(bytes).map_err(|e| JsError::new(&e))
}

#[wasm_bindgen(js_name = parseEeg)]
pub fn parse_eeg(bytes: &[u8], n_channels: Option<usize>) -> JsValue {
    let n = n_channels.unwrap_or(2);
    let v = core::parse_eeg(bytes, n);
    let o = Object::new();
    set(&o, "data", &Int16Array::from(&v[..]).into());
    set(&o, "nChannels", &JsValue::from_f64(n as f64));
    set(&o, "nSamples", &JsValue::from_f64((v.len() / n) as f64));
    o.into()
}
