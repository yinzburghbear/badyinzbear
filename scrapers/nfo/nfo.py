import json
import os
import re
import sys
from pathlib import Path
from typing import List, Union
import xml.etree.ElementTree

# Add sibling/nearby Community folder without hardcoding your Windows user path
SCRIPT_PATH = Path(__file__).resolve()

for parent in [SCRIPT_PATH.parent, *SCRIPT_PATH.parents]:
    community_path = parent / "Community"
    if community_path.is_dir():
        sys.path.insert(0, str(community_path))
        break

import py_common.log as log
import py_common.graphql as graphql

# Allows us to simply debug the script via CLI args
if len(sys.argv) > 2 and '-d' in sys.argv:
    stdin = sys.argv[sys.argv.index('-d') + 1]
else:
    stdin = sys.stdin.read()

frag: dict = json.loads(stdin)
log.debug(f"NFO input fragment: {frag}")


def GetPathFromFragment(fragment: dict) -> str:
    """Try to find a media file path from the different shapes Stash may send."""
    if fragment is None:
        return None

    # Most direct/simple case
    path = fragment.get('path')
    if path:
        return path

    # Some Stash scrape inputs include the selected file(s) here
    files = fragment.get('files') or []
    if isinstance(files, list) and len(files) > 0:
        first_file = files[0]
        if isinstance(first_file, dict):
            path = first_file.get('path')
            if path:
                return path

    # Some inputs may nest the scene data
    scene_fragment = fragment.get('scene') or {}
    if isinstance(scene_fragment, dict):
        path = scene_fragment.get('path')
        if path:
            return path

        files = scene_fragment.get('files') or []
        if isinstance(files, list) and len(files) > 0:
            first_file = files[0]
            if isinstance(first_file, dict):
                path = first_file.get('path')
                if path:
                    return path

    return None


def GetPathFromScene(scene_dict: dict) -> str:
    """Try to find a media file path from a scene returned by GraphQL."""
    if scene_dict is None:
        return None

    path = scene_dict.get('path')
    if path:
        return path

    files = scene_dict.get('files') or []
    if isinstance(files, list) and len(files) > 0:
        first_file = files[0]
        if isinstance(first_file, dict):
            path = first_file.get('path')
            if path:
                return path

    return None


id = frag.get('id')
scene: dict = None
path_in_frag = GetPathFromFragment(frag)

if path_in_frag is None and id is not None:
    scene = graphql.getScene(id)
else:
    scene = {'path': path_in_frag}

scene_path: str = path_in_frag or GetPathFromScene(scene)
sceneByFragment = 'sceneByFragment' in sys.argv

log.debug(f"NFO scraper scene_path: {scene_path}")


def CreateNfoPathByScenePath(folder_nfo: bool = False) -> str:
    """Creates a path to the nfo file of the specific scene

    Args:
        folder_nfo (bool, optional): Whether to create path for the folder's nfo file or the scene itself. Defaults to False.

    Returns:
        string: Path to nfo file
    """
    if not scene_path:
        log.error("NFO scraper could not determine scene path, so no .nfo path can be created.")
        return None

    dir: str = os.path.dirname(scene_path)
    nfo_filename = "folder" if folder_nfo == True else os.path.splitext(
        os.path.basename(scene_path))[0]

    # First check for the normal same-folder style:
    # Movie.mp4 -> Movie.nfo
    nfo_path: str = os.path.join(dir, nfo_filename + ".nfo")
    log.debug(f"Checking NFO path: {nfo_path}")
    if os.path.isfile(nfo_path):
        return nfo_path

    # Then check the alternate hidden .nfo folder style:
    # Movie.mp4 -> .nfo/Movie.nfo
    nfo_path: str = os.path.join(
        dir, ".nfo" + os.path.sep, nfo_filename + ".nfo")
    log.debug(f"Checking NFO path: {nfo_path}")
    return nfo_path


class Performer:
    def __init__(self, name: str, image: str = ''):
        self.name = name
        self.url: str = ''
        self.image: str = image
        self.gender: str = ''
        self.twitter: str = ''
        self.instagram: str = ''
        self.birthdate: str = ''
        self.deathdate: str = ''
        self.ethnicity: str = ''
        self.country: str = ''
        self.haircolor: str = ''
        self.eyecolor: str = ''
        self.height: str = ''
        self.weight: str = ''
        self.measurements: str = ''
        self.faketits: str = ''
        self.careerlength: str = ''
        self.tattoos: str = ''
        self.piercings: str = ''
        self.aliases: str = ''
        self.tags: List[Tag] = []
        self.details: str = ''

        # Private
        self.singulars = {
            'aliases': 'alias',
            'tags': 'tag',
        }
        self.plurals = {
            'alias': 'aliases',
            'tag': 'tags',
        }

    def ToDict(self):
        return {
            'name': PostProcess(self.name),
            'url': PostProcess(self.url),
            'image': PostProcess(self.image),
            'gender': PostProcess(self.gender),
            'twitter': PostProcess(self.twitter),
            'instagram': PostProcess(self.instagram),
            'birthdate': PostProcess(self.birthdate),
            'deathdate': PostProcess(self.deathdate),
            'ethnicity': PostProcess(self.ethnicity),
            'country': PostProcess(self.country),
            'haircolor': PostProcess(self.haircolor),
            'eyecolor': PostProcess(self.eyecolor),
            'height': PostProcess(self.height),
            'weight': PostProcess(self.weight),
            'measurements': PostProcess(self.measurements),
            'faketits': PostProcess(self.faketits),
            'careerlength': PostProcess(self.careerlength),
            'tattoos': PostProcess(self.tattoos),
            'piercings': PostProcess(self.piercings),
            'aliases': PostProcess(self.aliases),
            'tags': [tag.ToDict() for tag in self.tags],
            'details': PostProcess(self.details),
        }


class Studio:
    def __init__(self, name: str, logo: str = ''):
        self.name = name
        self.details: str = ''
        self.url: str = ''
        self.logo: str = logo
        # Stash expects ScrapedStudio.aliases as a string, not a JSON array.
        self.aliases: str = ''

        # Private
        self.singulars = {
            'aliases': 'alias',
        }
        self.plurals = {
            'alias': 'aliases',
        }

    def ToDict(self):
        return {
            'name': PostProcess(self.name),
            'details': PostProcess(self.details),
            'url': PostProcess(self.url),
            'logo': PostProcess(self.logo),
            'aliases': PostProcess(self.aliases),
        }


class Tag:
    def __init__(self, name: str):
        self.name = name

    def ToDict(self):
        return {'name': PostProcess(self.name)}


class Scene:
    def __init__(self):
        self.id: str = ''
        self.url: str = ''
        self.title: str = ''
        self.performers: List[Performer] = []
        self.details: str = ''
        self.date: str = ''
        self.studio: Studio = None
        self.image: str = ''
        self.rating: int = 0
        self.tags: List[Tag] = []

    def ToDict(self):
        return {
            # 'id': self.id, # We not going to change id and it isn't needed anyway
            # 'url': PostProcess(self.url),
            'title': PostProcess(self.title),
            'details': PostProcess(self.details),
            'date': PostProcess(self.date),
            'image': PostProcess(self.image),
            # 'rating': self.rating, # Well well, this is a personal thing i guess, so we wont set it because nfo files might be shared
            'studio': None if self.studio is None else self.studio.ToDict(),
            'performers': [performer.ToDict() for performer in self.performers],
            'tags': [tag.ToDict() for tag in self.tags],
        }


SceneObject: Scene = Scene()


def PostProcess(value: str) -> str:
    if value is None or '%' not in value:
        # Performance reasons...
        return value
    pp_dict = {
        'title': SceneObject.title if SceneObject.title is not None and len(SceneObject.title) > 0 else scene.get('title', ''),
        'filename': os.path.splitext(os.path.basename(scene_path))[0],
        'fileextension': os.path.splitext(os.path.basename(scene_path))[1],
        'studio_name': (SceneObject.studio.name if SceneObject.studio is not None else '') if SceneObject.studio is not None else scene.get('studio', ''),
        'date': SceneObject.date if SceneObject.date is not None and len(SceneObject.date) > 0 else scene.get('date', ''),
    }

    for variable in pp_dict:
        variable_value: str = pp_dict.get(variable) or ''
        value = re.sub(f'%{variable}%', variable_value, value)

    return value


def return_result(result: Scene):
    """
    Return the result to stash and exit the script.

    Returns:
        None: Totally exit the scraper via sys.exit(0)
    """
    try:
        if result is None:
            print(json.dumps({}))
        else:
            str_result = json.dumps(result.ToDict())
            print(str_result)
        sys.exit(0)
    except Exception as err:
        log.error(f"Exception occurred during returning result: {err}")


class NFO:
    def __init__(self, nfo_path: str) -> None:
        self.nfo_path = nfo_path
        self.nfo = None
        self.nfo_path = self.nfo_path if self.__fix_file() else None

    def __fix_file(self) -> bool:
        """By now, this function eliminates empty line breaks at the start of a file
        until it reaches the <?xml

        Python's xml parser throws exception in such cases, and `kodi-helper` creates such files.

        """
        if self.nfo_path is None or os.path.isfile(self.nfo_path) == False:
            return False

        with open(self.nfo_path, 'r+') as f:
            counter: int = 0
            line = ''
            # First of all, before eliminating the lines, check if it's really xml/nfo file.
            data = f.read()
            if '<?xml' not in data or '?>' not in data or '<movie>' not in data:
                return False
            f.seek(0)
            data = ''

            while True:
                line = f.readline()
                if len(line.strip()) > 0 and line.lower().startswith('<?xml'):
                    break
                else:
                    counter += 1
            if counter > 0:
                # File has to be fixed/modified
                data = line + f.read()
                f.seek(0)
                f.write(data)
                f.truncate()
        return True

    def __parse_actors(self, actors: List[xml.etree.ElementTree.Element]):
        for actor in actors:
            performer = Performer(actor.findtext(
                'name'), actor.findtext('thumb'))
            performer_dict = performer.ToDict()
            for key in performer_dict:
                value: Union[str, list] = performer_dict[key]
                if isinstance(value, str):
                    # Trim the value if is string
                    value = value.strip()
                if value is None or len(value) == 0:
                    # Value is empty, let's try to set it by it's actor's equivalent element
                    # We check for both singular and plural keys,
                    # example: including several <aliases> with a single alias is not correct in grammar
                    # so we check for <alias> too.
                    keys_to_check: List[str] = []
                    keys_to_check.append(key)
                    if key in performer.singulars:
                        keys_to_check.append(performer.singulars.get(key))
                    if key in performer.plurals:
                        keys_to_check.append(performer.plurals.get(key))
                    result_list: List[str] = []
                    for key_to_check in keys_to_check:
                        results = actor.findall(key_to_check)
                        if len(results) > 0:
                            result_list.extend(
                                [result.text for result in results])
                    # Set the Performer's attribute value.
                    setattr(performer, key, " / ".join(result_list)
                            if isinstance(value, list) == False else result_list)

            SceneObject.performers.append(performer)

    def __parse_studio(self, studio: xml.etree.ElementTree.Element):
        if studio is None:
            return
        studio_has_child: bool = studio.__len__() > 0
        if studio_has_child == False:
            return
        if SceneObject.studio is None:
            SceneObject.studio = Studio('')
        studio_dict = SceneObject.studio.ToDict()
        for key in studio_dict:
            value: Union[str, list] = studio_dict[key]
            if isinstance(value, str):
                # Trim the value if is string
                value = value.strip()
            if value is None or len(value) == 0:
                # Value is empty, let's try to set it by it's actor's equivalent element
                # Value is empty, let's try to set it by it's actor's equivalent element
                # We check for both singular and plural keys,
                # example: including several <aliases> with a single alias is not correct in grammar
                # so we check for <alias> too.
                keys_to_check: List[str] = []
                keys_to_check.append(key)
                if key in SceneObject.studio.singulars:
                    keys_to_check.append(SceneObject.studio.singulars.get(key))
                if key in SceneObject.studio.plurals:
                    keys_to_check.append(SceneObject.studio.plurals.get(key))
                result_list: List[str] = []
                for key_to_check in keys_to_check:
                    results = studio.findall(key_to_check)
                    if len(results) > 0:
                        result_list.extend(
                            [result.text for result in results])
                # Stash expects Studio aliases as a string, so always flatten lists.
                setattr(SceneObject.studio, key, " / ".join([r for r in result_list if r]))

    def parse(self) -> bool:
        if self.nfo_path is None or os.path.isfile(self.nfo_path) == False:
            return False

        # create element tree object
        tree = xml.etree.ElementTree.parse(self.nfo_path)

        # get root element
        movie = tree.getroot()

        unique_ids = movie.findall('uniqueid')
        for unique_id in unique_ids:
            type: str = unique_id.get('type').lower()
            if type == 'stash':
                SceneObject.id = unique_id.text

        SceneObject.url = movie.findtext('url') or SceneObject.url

        SceneObject.title = movie.findtext('title') or movie.findtext(
            'originaltitle') or movie.findtext('sorttitle') or SceneObject.title

        self.__parse_actors(movie.findall('actor'))

        SceneObject.details = movie.findtext('plot') or movie.findtext(
            'outline') or movie.findtext('tagline') or SceneObject.details

        SceneObject.date = movie.findtext('premiered') or movie.findtext(
            'year') or movie.findtext('releasedate') or SceneObject.date

        # Kodi backward compatibility
        SceneObject.studio = Studio(
            movie.findtext('studio')) or SceneObject.studio

        # A full Studio model which can have name, url, and logo child elements
        studio_with_childs = movie.find('studiomodel')
        self.__parse_studio(studio_with_childs)

        thumbs = movie.findall('thumb')
        for thumb in thumbs:
            aspect: str = thumb.get('aspect').lower()
            if aspect == 'poster':
                SceneObject.image = thumb.text or SceneObject.image
            elif aspect == 'clearlogo':
                SceneObject.studio.logo = thumb.text or SceneObject.studio.logo

        SceneObject.rating = movie.findtext('userrating') or movie.findtext(
            'rating') or movie.findtext('ratings') or SceneObject.rating

        tags = movie.findall('tag')
        tag_list = [Tag(tag.text) for tag in tags]
        if SceneObject.tags is not None and len(tag_list) > 0:
            SceneObject.tags.extend(tag_list)
        else:
            SceneObject.tags = tag_list

        return True


# First, we parse the folder.nfo if exists, For example, a folder might be for an specific studio so it can be set by that
# Or the folder might be the scenes of a movie, so they have equal dates and can be used for the scenes.
# folder.nfo can be created manually, making it easy if you want to set metadata for scenes in an specific folder.
folderNfo: NFO = NFO(CreateNfoPathByScenePath(folder_nfo=True))
foldernfo_parse_result = folderNfo.parse()

nfo: NFO = NFO(CreateNfoPathByScenePath())
parse_result = nfo.parse()

if parse_result == False and foldernfo_parse_result == False:
    SceneObject = None

return_result(SceneObject)

# Last Updated 7/9/2026
