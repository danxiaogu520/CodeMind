export function recordLogin(userId) {
  return { event: "user.login", userId };
}
