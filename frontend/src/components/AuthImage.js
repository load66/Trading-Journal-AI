import { useEffect, useState } from 'react';
import { diaryFileApi } from '../api';

export default function AuthImage({ name, alt = '', ...props }) {
  const [src, setSrc] = useState('');

  useEffect(() => {
    let active = true;
    let objectUrl = '';
    diaryFileApi.get(name)
      .then((response) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(response.data);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (active) setSrc('');
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [name]);

  if (!src) return null;
  return <img src={src} alt={alt} {...props} />;
}
