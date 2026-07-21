pub struct TokenIssuer;

impl TokenIssuer {
    pub fn issue(&self, subject: &str) -> String {
        format!("rust-token:{subject}")
    }
}

pub fn authenticate(subject: &str) -> String {
    TokenIssuer.issue(subject)
}
