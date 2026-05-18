import { R2_PREFIX_BOOKS } from "../constants";

function bookPrefix(author: string, title: string): string {
	return `${R2_PREFIX_BOOKS}/${author}/${title}`;
}

function chapterFilename(chapter: string | number): string {
	return `chapter-${String(chapter).padStart(2, "0")}.m4a`;
}

export const r2Key = {
	cover: (author: string, title: string, ext = "jpg") =>
		`${bookPrefix(author, title)}/cover.${ext}`,

	epub: (author: string, title: string) => `${bookPrefix(author, title)}/book.epub`,

	bookManifest: (author: string, title: string) => `${bookPrefix(author, title)}/manifest.json`,

	buildPrefix: (author: string, title: string, rendition: string, build: string) =>
		`${bookPrefix(author, title)}/audio/${rendition}/builds/${build}`,

	audio: (
		author: string,
		title: string,
		rendition: string,
		build: string,
		chapter: string | number,
	) => `${r2Key.buildPrefix(author, title, rendition, build)}/${chapterFilename(chapter)}`,

	chapterData: (author: string, title: string, rendition: string, build: string) =>
		`${r2Key.buildPrefix(author, title, rendition, build)}/chapter_data.json`,

	renditionManifest: (author: string, title: string, rendition: string, build: string) =>
		`${r2Key.buildPrefix(author, title, rendition, build)}/rendition-manifest.json`,
};
