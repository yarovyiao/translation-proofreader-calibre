from calibre.customize import InterfaceActionBase


__license__ = 'GPL v3'
__copyright__ = '2026, Translation Proofreader contributors'


class TranslationProofreaderPlugin(InterfaceActionBase):
    name = 'Translation Proofreader'
    title = 'Translation Proofreader'
    supported_platforms = ['windows', 'osx', 'linux']
    identifier = 'translation-proofreader'
    author = 'Translation Proofreader contributors'
    version = (0, 11, 15)
    description = (
        'Compare an original EPUB with its translation and proofread the '
        'translation with an AI API.'
    )
    minimum_calibre_version = (7, 0, 0)
    actual_plugin = (
        'calibre_plugins.translation_proofreader.ui:'
        'TranslationProofreaderAction'
    )

    def is_customizable(self):
        return False
