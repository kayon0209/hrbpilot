import { render, screen } from "@testing-library/react";

import { App } from "../src/app/App";

describe("App", () => {
  it("renders the frontend shell", () => {
    render(<App />);

    expect(screen.getByText("HRBPilot")).toBeInTheDocument();
  });
});
