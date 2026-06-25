import { R2_PREFIX_BOOKS } from "../constants";

function bookPrefix(author: string, title: string): string {
	return `${R2_PREFIX_BOOKS}/${author}/${title}`;
}

function sectionFilename(sequence: string | number): string {
	return `section-${String(sequence).padStart(2, "0")}.m4a`;
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
		sequence: string | number,
	) => `${r2Key.buildPrefix(author, title, rendition, build)}/${sectionFilename(sequence)}`,

	synthesisUnits: (
		author: string,
		title: string,
		rendition: string,
		build: string,
		sequence: string | number,
	) =>
		`${r2Key.buildPrefix(author, title, rendition, build)}/section-${String(sequence).padStart(2, "0")}.synthesis_units.json`,

	sectionData: (author: string, title: string, rendition: string, build: string) =>
		`${r2Key.buildPrefix(author, title, rendition, build)}/section_data.json`,

	runContext: (author: string, title: string, rendition: string, build: string) =>
		`${r2Key.buildPrefix(author, title, rendition, build)}/run.json`,

	renditionManifest: (author: string, title: string, rendition: string, build: string) =>
		`${r2Key.buildPrefix(author, title, rendition, build)}/rendition-manifest.json`,
};
