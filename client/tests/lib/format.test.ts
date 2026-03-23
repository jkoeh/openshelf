import { describe, expect, it } from "vitest";
import { formatDuration, formatTime, stringToHue } from "../../lib/format";

describe("formatDuration", () => {
	it("formats hours and minutes", () => {
		expect(formatDuration(3600)).toBe("1h 0m");
		expect(formatDuration(5400)).toBe("1h 30m");
		expect(formatDuration(28800)).toBe("8h 0m");
	});

	it("formats minutes only when under an hour", () => {
		expect(formatDuration(300)).toBe("5m");
		expect(formatDuration(2880)).toBe("48m");
	});

	it("rounds minutes", () => {
		expect(formatDuration(90)).toBe("2m");
		expect(formatDuration(89)).toBe("1m");
	});

	it("handles zero", () => {
		expect(formatDuration(0)).toBe("0m");
	});
});

describe("formatTime", () => {
	it("formats as M:SS", () => {
		expect(formatTime(0)).toBe("0:00");
		expect(formatTime(5)).toBe("0:05");
		expect(formatTime(65)).toBe("1:05");
		expect(formatTime(600)).toBe("10:00");
	});

	it("truncates fractional seconds", () => {
		expect(formatTime(65.7)).toBe("1:05");
	});
});

describe("stringToHue", () => {
	it("returns a number between 0 and 359", () => {
		const hue = stringToHue("The Trial");
		expect(hue).toBeGreaterThanOrEqual(0);
		expect(hue).toBeLessThan(360);
	});

	it("is deterministic", () => {
		expect(stringToHue("Crime and Punishment")).toBe(stringToHue("Crime and Punishment"));
	});

	it("produces different hues for different strings", () => {
		expect(stringToHue("The Trial")).not.toBe(stringToHue("Crime and Punishment"));
	});
});
