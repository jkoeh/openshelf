interface FinishTransitionArgs {
	didJustFinish: boolean;
	currentSection: number;
	totalSections: number;
	pinnedSection?: number;
	rendition?: string;
	build?: string;
	handledFinishKey: string | null;
	pendingAutoplaySection: number | null;
}

interface FinishTransition {
	nextSection: number;
	pendingAutoplaySection: number;
	handledFinishKey: string;
}

export function sectionPlaybackKey(section: number, rendition: string, build: string): string {
	return `${rendition}:${build}:${section}`;
}

export function nextAutoplaySectionAfterFinish({
	didJustFinish,
	currentSection,
	totalSections,
	pinnedSection,
	rendition,
	build,
	handledFinishKey,
	pendingAutoplaySection,
}: FinishTransitionArgs): FinishTransition | null {
	if (!didJustFinish || totalSections <= 0 || currentSection >= totalSections) return null;
	if (!rendition || !build || pinnedSection !== currentSection) return null;
	if (pendingAutoplaySection !== null) return null;

	const finishKey = sectionPlaybackKey(currentSection, rendition, build);
	if (handledFinishKey === finishKey) return null;
	const nextSection = currentSection + 1;
	return {
		nextSection,
		pendingAutoplaySection: nextSection,
		handledFinishKey: finishKey,
	};
}

export function shouldPlayPendingSection({
	pendingAutoplaySection,
	currentSection,
	pinnedSection,
	isLoaded,
	hasAudioSource,
}: {
	pendingAutoplaySection: number | null;
	currentSection: number;
	pinnedSection?: number;
	isLoaded: boolean;
	hasAudioSource: boolean;
}): boolean {
	return (
		pendingAutoplaySection === currentSection &&
		pinnedSection === currentSection &&
		isLoaded &&
		hasAudioSource
	);
}
