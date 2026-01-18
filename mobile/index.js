import write_blob from 'capacitor-blob-writer';
import { Filesystem, Directory } from '@capacitor/filesystem';
import { FileOpener } from '@capawesome-team/capacitor-file-opener';
import { App } from '@capacitor/app';

console.log("Mobile shell loaded");

App.addListener('backButton', () => {
    const iframe = document.getElementById('app-frame');
    
    if (iframe && iframe.contentWindow && iframe.contentWindow.location) {
        const href = iframe.contentWindow.location.href;
        
        if (href.includes('popup.html')) {
            App.exitApp();
        } else {
            iframe.contentWindow.history.back();
        }
    } else {
        App.exitApp();
    }
});

window.handleMobileDownload = async (blob, filename) => {
    try {
        await write_blob({
            path: filename,
            directory: Directory.Cache,
            blob: blob,
            recursive: true
        });

        const uriResult = await Filesystem.getUri({
            path: filename,
            directory: Directory.Cache
        });

        await FileOpener.openFile({
            path: uriResult.uri
        });

        return true;
    } catch (error) {
        console.error(error);
        throw error;
    }
};
