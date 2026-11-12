# Artwork

Each section of the site has its own painting, and so does the subscribe pop-up. All are public domain (The Metropolitan Museum of Art, Open
Access, CC0). The files are the Met's images with the frame edge cropped off, resized to 2200 and 1100 px
wide, and converted to WebP. The pop-up's painting is upright, so it is one file, 900 px wide. The site
does not show a credit for the section paintings; this file records the sources. The pop-up names its
painting in a caption, because there the painting was chosen for what it shows: a reader.

| Files | Used on | Painting |
| --- | --- | --- |
| `pompton-plains-*` | home | Jasper Francis Cropsey, *Pompton Plains, New Jersey*, 1867. [Met object 10584](https://www.metmuseum.org/art/collection/search/10584) |
| `delaware-water-gap-*` | labs | George Inness, *Delaware Water Gap*, 1861. [Met object 11229](https://www.metmuseum.org/art/collection/search/11229) |
| `eatons-neck-*` | writing, posts | John Frederick Kensett, *Eaton's Neck, Long Island*, 1872. [Met object 11307](https://www.metmuseum.org/art/collection/search/11307) |
| `spring-blossoms-*` | members, profiles | George Inness, *Spring Blossoms, Montclair, New Jersey*, ca. 1891. [Met object 11234](https://www.metmuseum.org/art/collection/search/11234) |
| `newburyport-meadows-*` | editor, settings, admin | Martin Johnson Heade, *Newburyport Meadows*, ca. 1876–81. [Met object 11053](https://www.metmuseum.org/art/collection/search/11053) |
| `woman-reading-900.webp` | subscribe pop-up | Camille Corot, *A Woman Reading*, 1869 and 1870. [Met object 435991](https://www.metmuseum.org/art/collection/search/435991) |

To change a section's painting, add the two files here and edit the `.art-*` rules near the top of
`static/site.css`. Templates pick a painting with `{% block art %}name{% endblock %}`.
