export interface Credentials {
  email: string;
  password: string;
}

export function submitLogin(credentials: Credentials): string {
  return `/api/login?email=${encodeURIComponent(credentials.email)}`;
}
