export function getSubmitButtonState({ loading }) {
  return {
    label: loading ? "Signing in..." : "Sign in",
    disabled: false
  };
}
