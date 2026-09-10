//! The wire between the host bridge and this daemon: one JSON request
//! per line, one response, and the errors both sides agree on.

use super::*;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GuestRequest {
    pub version: u64,
    #[serde(default)]
    pub capability: Option<String>,
    pub operation: String,
    #[serde(default)]
    pub parameters: Value,
}

#[derive(Debug, Serialize)]
pub struct GuestResponse {
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<GuestError>,
}

#[derive(Clone, Debug, Serialize)]
pub struct GuestError {
    pub code: String,
    pub message: String,
    pub retryable: bool,
    pub status_code: u16,
}

impl GuestError {
    pub(crate) fn invalid(message: impl Into<String>) -> Self {
        Self {
            code: "invalid_request".into(),
            message: message.into(),
            retryable: false,
            status_code: 422,
        }
    }

    pub(crate) fn not_found() -> Self {
        Self {
            code: "not_found".into(),
            message: "Sandbox not found".into(),
            retryable: false,
            status_code: 404,
        }
    }

    pub(crate) fn engine(message: impl Into<String>) -> Self {
        Self {
            code: "guest_engine_failed".into(),
            message: message.into(),
            retryable: true,
            status_code: 503,
        }
    }
}

impl GuestResponse {
    pub(crate) fn success(result: Value) -> Self {
        Self {
            ok: true,
            result: Some(result),
            error: None,
        }
    }

    pub(crate) fn failure(error: GuestError) -> Self {
        Self {
            ok: false,
            result: None,
            error: Some(error),
        }
    }
}

pub fn handle_reader<R: Read, W: Write, E: Engine + 'static>(
    reader: R,
    mut writer: W,
    service: &GuestService<E>,
) -> io::Result<bool> {
    let mut bounded = BufReader::new(reader).take(MAX_REQUEST_BYTES + 1);
    let mut line = String::new();
    bounded.read_line(&mut line)?;
    let response = response_for_line(&line, service);
    write_response(&mut writer, &response)?;
    Ok(response.ok)
}

pub(crate) fn response_for_line<E: Engine + 'static>(
    line: &str,
    service: &GuestService<E>,
) -> GuestResponse {
    if line.len() as u64 > MAX_REQUEST_BYTES {
        GuestResponse::failure(GuestError::invalid("request exceeded 1 MiB"))
    } else {
        match serde_json::from_str::<GuestRequest>(line.trim_end()) {
            Ok(request) => service.handle(request),
            Err(error) => GuestResponse::failure(GuestError::invalid(format!(
                "invalid request JSON: {error}"
            ))),
        }
    }
}

pub(crate) fn write_response<W: Write>(writer: &mut W, response: &GuestResponse) -> io::Result<()> {
    let encoded = serde_json::to_vec(&response)?;
    if encoded.len() > MAX_RESPONSE_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "guest response exceeded 4 MiB",
        ));
    }
    writer.write_all(&encoded)?;
    writer.write_all(b"\n")?;
    writer.flush()?;
    Ok(())
}

#[cfg(any(target_os = "linux", test))]
pub(crate) fn handle_stream<R: Read, W: Write, E: Engine + 'static>(
    reader: R,
    mut writer: W,
    service: &GuestService<E>,
) -> io::Result<()> {
    let mut reader = BufReader::new(reader);
    loop {
        let mut line = String::new();
        let count = (&mut reader)
            .take(MAX_REQUEST_BYTES + 1)
            .read_line(&mut line)?;
        if count == 0 {
            return Ok(());
        }
        let response = response_for_line(&line, service);
        write_response(&mut writer, &response)?;
    }
}
