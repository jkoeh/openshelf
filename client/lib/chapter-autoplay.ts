interface FinishTransitionArgs {
	didJustFinish: boolean;
	currentChapter: number;
	totalChapters: number;
	pinnedChapter?: number;
	rendition?: string;
	build?: string;
	handledFinishKey: string | null;
	pendingAutoplayChapter: number | null;
}

interface FinishTransition {
	nextChapter: number;
	pendingAutoplayChapter: number;
	handledFinishKey: string;
}

export function chapterPlaybackKey(chapter: number, rendition: string, build: string): string {
	return `${rendition}:${build}:${chapter}`;
}

export function nextAutoplayChapterAfterFinish({
	didJustFinish,
	currentChapter,
	totalChapters,
	pinnedChapter,
	rendition,
	build,
	handledFinishKey,
	pendingAutoplayChapter,
}: FinishTransitionArgs): FinishTransition | null {
	if (!didJustFinish || totalChapters <= 0 || currentChapter >= totalChapters) return null;
	if (!rendition || !build || pinnedChapter !== currentChapter) return null;
	if (pendingAutoplayChapter !== null) return null;

	const finishKey = chapterPlaybackKey(currentChapter, rendition, build);
	if (handledFinishKey === finishKey) return null;

	const nextChapter = currentChapter + 1;
	return {
		nextChapter,
		pendingAutoplayChapter: nextChapter,
		handledFinishKey: finishKey,
	};
}

export function shouldPlayPendingChapter({
	pendingAutoplayChapter,
	currentChapter,
	pinnedChapter,
	isLoaded,
	hasAudioSource,
}: {
	pendingAutoplayChapter: number | null;
	currentChapter: number;
	pinnedChapter?: number;
	isLoaded: boolean;
	hasAudioSource: boolean;
}): boolean {
	return (
		pendingAutoplayChapter === currentChapter &&
		pinnedChapter === currentChapter &&
		isLoaded &&
		hasAudioSource
	);
}
