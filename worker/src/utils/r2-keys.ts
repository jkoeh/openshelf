import { R2_DEFAULT_RENDITION, R2_PREFIX_BOOKS } from "../constants";

function bookPrefix(author: string, title: string): string {
	return `${R2_PREFIX_BOOKS}/${author}/${title}`;
}

function renditionPrefix(author: string, title: string, rendition = R2_DEFAULT_RENDITION): string {
	return `${bookPrefix(author, title)}/audio/${rendition}`;
}

export const r2Key = {
	cover: (author: string, title: string, ext = "jpg") =>
		`${bookPrefix(author, title)}/cover.${ext}`,

	epub: (author: string, title: string) => `${bookPrefix(author, title)}/book.epub`,

	manifest: (author: string, title: string, rendition?: string) =>
		`${renditionPrefix(author, title, rendition)}/manifest.json`,

	audio: (author: string, title: string, chapter: string, rendition?: string) =>
		`${renditionPrefix(author, title, rendition)}/chapter-${chapter}.m4a`,

	chapterData: (author: string, title: string, rendition?: string) =>
		`${renditionPrefix(author, title, rendition)}/chapter_data.json`,
};
