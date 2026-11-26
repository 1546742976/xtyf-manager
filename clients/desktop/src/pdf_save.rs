use base64::{engine::general_purpose::STANDARD, Engine};

pub fn decode(value: &str) -> Result<Vec<u8>, String> {
    if value.len() > 28 * 1024 * 1024 { return Err("PDF 超过保存限制".into()); }
    let bytes = STANDARD.decode(value).map_err(|_| "PDF 内容无效")?;
    if bytes.len() > 20 * 1024 * 1024 || !bytes.starts_with(b"%PDF-") {
        return Err("PDF 内容无效".into());
    }
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn retains_exact_pdf_and_rejects_other_content() {
        let source = b"%PDF-1.7\nsynthetic\n%%EOF";
        assert_eq!(decode(&STANDARD.encode(source)).unwrap(), source);
        for value in ["?".to_string(), STANDARD.encode(b"<html>"), "A".repeat(28*1024*1024+1)] {
            assert!(decode(&value).is_err());
        }
    }
}
