import { render, screen } from "@testing-library/react";

import { App } from "../src/app/App";

describe("App", () => {
  it("restores to the stable login screen without a session", async () => {
    render(<App />);

    expect(await screen.findByLabelText("邮箱")).toBeInTheDocument();
  });
});
